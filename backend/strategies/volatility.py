# INTELLISTOCK_SCHEMA: {"strategy": "Volatility", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"model_path": "<optional>", "lookback_days": 20}}
# INTELLISTOCK_DESCRIPTION: TCN + ANP volatility forecast with GBT directional classifier. Two-stage ML: volatility forecast then buy/hold/sell.
# DIFFICULTY: 9
"""
Volatility Forecast Strategy: TCN + ANP-style volatility forecasting with GBT directional classifier.

This strategy uses a two-stage ML pipeline:
1. TCN + ANP-style model to forecast realized volatility
2. Gradient Boosting Tree classifier for directional prediction (buy/hold/sell)

Strategy name from DB: "VolatilityForecast" (file volatility_forecast.py, class Volatilityforecast).
Returns: 1 = buy, 0 = hold, -1 = sell.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterator, Tuple, Optional

# Suppress PyTorch C++ NNPACK "Unsupported hardware" warnings (glog; must be set before torch)
os.environ.setdefault("USE_NNPACK", "0")
os.environ.setdefault("GLOG_minloglevel", "2")  # 0=INFO, 1=WARNING, 2=ERROR, 3=FATAL; 2 hides NNPACK WARNING

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.ensemble import HistGradientBoostingClassifier

torch.backends.nnpack.enabled = False

# Try to import broker's logger, fallback to print
try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="VolatilityStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[VolatilityStrategy] {msg}")


class CausalConv1d(nn.Conv1d):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.left_padding = (self.kernel_size[0] - 1) * self.dilation[0]

    def forward(self, x):
        x = F.pad(x, (self.left_padding, 0))
        return super().forward(x)


class TCNBlock(nn.Module):
    def __init__(self, channels: int, kernel_size: int, dilation: int, dropout: float):
        super().__init__()
        self.conv1 = CausalConv1d(channels, channels, kernel_size, dilation=dilation)
        self.conv2 = CausalConv1d(channels, channels, kernel_size, dilation=dilation)
        self.dropout = nn.Dropout(dropout)
        self.norm1 = nn.LayerNorm(channels)
        self.norm2 = nn.LayerNorm(channels)

    def forward(self, x):
        res = x
        y = self.conv1(x.transpose(1, 2)).transpose(1, 2)
        y = self.norm1(y)
        y = F.gelu(y)
        y = self.dropout(y)
        y = self.conv2(y.transpose(1, 2)).transpose(1, 2)
        y = self.norm2(y)
        y = F.gelu(y)
        y = self.dropout(y)
        return y + res


class TCNEncoder(nn.Module):
    def __init__(self, in_features: int, channels: int = 64, levels: int = 4, kernel_size: int = 3, dropout: float = 0.1):
        super().__init__()
        self.proj = nn.Linear(in_features, channels)
        self.blocks = nn.ModuleList(
            [TCNBlock(channels, kernel_size, dilation=2**i, dropout=dropout) for i in range(levels)]
        )

    def forward(self, x):
        h = self.proj(x)
        for blk in self.blocks:
            h = blk(h)
        return h[:, -1, :]


class MLP(nn.Module):
    def __init__(self, d_in: int, d_hidden: int, d_out: int, depth: int = 3, dropout: float = 0.1):
        super().__init__()
        layers = []
        d = d_in
        for _ in range(depth - 1):
            layers += [nn.Linear(d, d_hidden), nn.GELU(), nn.Dropout(dropout)]
            d = d_hidden
        layers += [nn.Linear(d, d_out)]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)


class CrossAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, n_heads, dropout=dropout, batch_first=True)

    def forward(self, q, k, v, key_padding_mask=None):
        out, _ = self.attn(q, k, v, key_padding_mask=key_padding_mask, need_weights=False)
        return out


class ANPTCN(nn.Module):
    def __init__(self, in_features: int, emb_dim: int = 64, z_dim: int = 32):
        super().__init__()
        self.tcn = TCNEncoder(in_features, channels=emb_dim)
        self.ctx_val = MLP(emb_dim + 1, 128, emb_dim, depth=3, dropout=0.1)
        self.attn = CrossAttention(d_model=emb_dim, n_heads=4, dropout=0.1)
        self.latent_enc = MLP(emb_dim + 1, 128, 2 * z_dim, depth=3, dropout=0.1)
        self.dec = MLP(emb_dim + emb_dim + z_dim, 128, 2, depth=3, dropout=0.1)
        self.emb_dim = emb_dim
        self.z_dim = z_dim

    @staticmethod
    def _reparam(mu, logvar):
        eps = torch.randn_like(mu)
        return mu + eps * torch.exp(0.5 * logvar)

    def _latent_dist(self, x_emb: torch.Tensor, y: torch.Tensor, mask: torch.Tensor):
        xy = torch.cat([x_emb, y], dim=-1)
        stats = self.latent_enc(xy)
        m = mask.float().unsqueeze(-1)
        stats = (stats * m).sum(dim=1) / (m.sum(dim=1).clamp_min(1.0))
        mu, logvar = stats[..., : self.z_dim], stats[..., self.z_dim :]
        logvar = logvar.clamp(-20.0, 2.0)
        return mu, logvar

    def forward(self, x_ctx, y_ctx, x_tgt, y_tgt=None, mask_ctx=None, mask_tgt=None):
        B, Nc, W, F = x_ctx.shape
        Nt = x_tgt.shape[1]
        x_ctx_emb = self.tcn(x_ctx.reshape(B * Nc, W, F)).reshape(B, Nc, self.emb_dim)
        x_tgt_emb = self.tcn(x_tgt.reshape(B * Nt, W, F)).reshape(B, Nt, self.emb_dim)
        v_ctx = self.ctx_val(torch.cat([x_ctx_emb, y_ctx], dim=-1))
        det = self.attn(
            q=x_tgt_emb,
            k=x_ctx_emb,
            v=v_ctx,
            key_padding_mask=(~mask_ctx) if mask_ctx is not None else None,
        )
        if mask_ctx is None:
            mask_ctx = torch.ones((B, Nc), dtype=torch.bool, device=x_ctx.device)
        if mask_tgt is None:
            mask_tgt = torch.ones((B, Nt), dtype=torch.bool, device=x_ctx.device)
        prior_mu, prior_logvar = self._latent_dist(x_ctx_emb, y_ctx, mask_ctx)
        if y_tgt is not None:
            all_x = torch.cat([x_ctx_emb, x_tgt_emb], dim=1)
            all_y = torch.cat([y_ctx, y_tgt], dim=1)
            all_m = torch.cat([mask_ctx, mask_tgt], dim=1)
            post_mu, post_logvar = self._latent_dist(all_x, all_y, all_m)
        else:
            post_mu, post_logvar = prior_mu, prior_logvar
        z = self._reparam(post_mu, post_logvar)
        z_tiled = z[:, None, :].expand(B, Nt, self.z_dim)
        out = self.dec(torch.cat([x_tgt_emb, det, z_tiled], dim=-1))
        mu = out[..., :1].clamp(-20.0, 20.0)
        log_sigma = out[..., 1:].clamp(-8, 5)
        return mu, log_sigma, (prior_mu, prior_logvar, post_mu, post_logvar)

    @staticmethod
    def nll_gaussian(y, mu, log_sigma):
        """Negative log likelihood for Gaussian."""
        var = torch.exp(2 * log_sigma).clamp(min=1e-8)
        return 0.5 * math.log(2 * math.pi) + log_sigma + 0.5 * ((y - mu) ** 2) / var

    @staticmethod
    def kl_diag_gauss(mu_q, logvar_q, mu_p, logvar_p):
        """KL divergence between two diagonal Gaussians."""
        return 0.5 * torch.sum(
            logvar_p - logvar_q
            + (torch.exp(logvar_q) + (mu_q - mu_p) ** 2) / torch.exp(logvar_p)
            - 1.0,
            dim=-1,
        )


# Global cache for trained models (per symbol)
# Stores: {symbol: {"models": {...}, "last_bar_count": int, "last_daily_count": int}}
_model_cache = {}


def bars_to_daily(bars):
    """
    Convert intraday Alpaca bars to daily aggregated data.
    
    Alpaca bar format:
    {'c': close, 'h': high, 'l': low, 'n': trades, 'o': open, 't': timestamp, 'v': volume, 'vw': vwap}
    """
    if not bars or len(bars) < 10:
        return None
    
    # Convert bars to DataFrame
    df_data = []
    for bar in bars:
        # Extract timestamp
        t = bar.get('t')
        if t is None:
            continue
            
        # Handle timestamp conversion
        if isinstance(t, str):
            try:
                from dateutil import parser as dateutil_parser
                t = dateutil_parser.parse(t)
            except:
                try:
                    t = pd.to_datetime(t)
                except:
                    continue
        elif hasattr(t, 'timestamp'):  # datetime object
            pass
        else:
            continue
        
        # Extract Alpaca bar fields
        close_val = bar.get('c')  # close price
        open_val = bar.get('o')   # open price
        high_val = bar.get('h')   # high price
        low_val = bar.get('l')    # low price
        volume_val = bar.get('v') # volume
        vwap_val = bar.get('vw')  # volume weighted average price (optional)
        
        # Close is required
        if close_val is None:
            continue
        
        # Use defaults for missing values
        open_val = open_val if open_val is not None else close_val
        high_val = high_val if high_val is not None else close_val
        low_val = low_val if low_val is not None else close_val
        volume_val = volume_val if volume_val is not None else 0.0
        
        try:
            df_data.append({
                'timestamp': t,
                'open': float(open_val),
                'high': float(high_val),
                'low': float(low_val),
                'close': float(close_val),
                'volume': float(volume_val),
                'vwap': float(vwap_val) if vwap_val is not None else float(close_val),
            })
        except (ValueError, TypeError) as e:
            continue
    
    if not df_data:
        return None
    
    df = pd.DataFrame(df_data)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df = df.set_index('timestamp')
    
    # Group by day
    df['day'] = df.index.normalize()
    
    # Daily aggregates
    daily_open = df.groupby('day')['open'].first()   # First open of the day
    daily_high = df.groupby('day')['high'].max()     # Highest high of the day
    daily_low = df.groupby('day')['low'].min()       # Lowest low of the day
    daily_close = df.groupby('day')['close'].last()   # Last close of the day
    daily_vol = df.groupby('day')['volume'].sum()     # Total volume of the day
    
    # Compute intraday log returns (using close prices)
    df['logret'] = np.log(df['close']).diff()
    
    # Realized variance proxy: sum of squared intraday log returns
    rv_var = df.groupby('day')['logret'].apply(lambda x: float(np.sum(np.square(x.values[np.isfinite(x.values)]))))
    rv_vol = np.sqrt(rv_var)
    # Replace any NaN or zero values with a small positive value to avoid division issues
    rv_vol = rv_vol.fillna(1e-6)
    rv_vol = rv_vol.replace(0, 1e-6)
    
    # Daily range (high - low) as additional feature
    daily_range = daily_high - daily_low
    
    daily = pd.DataFrame({
        'open': daily_open,
        'high': daily_high,
        'low': daily_low,
        'close': daily_close,
        'volume': daily_vol,
        'range': daily_range,
        'rv_var': rv_var,
        'rv_vol': rv_vol
    }).dropna()
    
    if len(daily) < 5:
        return None
    
    # Daily returns and features
    daily['ret_1d'] = np.log(daily['close']).diff()
    daily['absret_1d'] = daily['ret_1d'].abs()
    daily['range_proxy'] = daily['absret_1d'].rolling(5).mean()
    daily = daily.dropna()
    
    return daily


def make_supervised(daily, feature_cols, label_col, window_w, delta):
    """Create supervised learning dataset from daily data."""
    df = daily.copy()
    y = df[label_col].shift(-delta)
    feats = df[feature_cols].values.astype(np.float32)
    N = len(df)
    
    X_list, y_list, t_list = [], [], []
    for i in range(window_w - 1, N - delta):
        Xw = feats[i - window_w + 1 : i + 1]
        yi = float(y.iloc[i])
        if np.isnan(yi):
            continue
        X_list.append(Xw)
        y_list.append(yi)
        t_list.append(df.index[i])
    
    if len(X_list) == 0:
        return None, None, None
    
    X_arr = np.stack(X_list, axis=0)
    y_arr = np.array(y_list, dtype=np.float32)[:, None]
    return X_arr, y_arr, t_list


def zscore_fit(y):
    """Compute mean and std for z-score normalization."""
    y_arr = np.asarray(y).reshape(-1)
    # Filter out NaN and infinite values
    y_finite = y_arr[np.isfinite(y_arr)]
    if len(y_finite) == 0:
        raise ValueError("No finite values in y for z-score normalization")
    mu = float(np.mean(y_finite))
    sd = float(np.std(y_finite))
    # Ensure standard deviation is not zero (add small epsilon)
    if sd < 1e-8:
        sd = 1e-8
    return mu, sd


def zscore_apply(y, mu, sd):
    return (y - mu) / sd


def time_split(
    X: np.ndarray, y: np.ndarray, t: pd.DatetimeIndex, train_frac=0.6, val_frac=0.2
) -> tuple[tuple[np.ndarray, np.ndarray, pd.DatetimeIndex], tuple[np.ndarray, np.ndarray, pd.DatetimeIndex], tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]]:
    """Split data by time (train/val/test)."""
    n = len(t)
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    X_train, y_train, t_train = X[:n_train], y[:n_train], t[:n_train]
    X_val, y_val, t_val = X[n_train : n_train + n_val], y[n_train : n_train + n_val], t[n_train : n_train + n_val]
    X_test, y_test, t_test = X[n_train + n_val :], y[n_train + n_val :], t[n_train + n_val :]
    return (X_train, y_train, t_train), (X_val, y_val, t_val), (X_test, y_test, t_test)


def make_episode_batches(
    X: np.ndarray,
    y: np.ndarray,
    episode_len: int,
    stride: int,
    ctx_frac: float,
    batch_size: int,
    seed: int,
) -> Iterator[Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]]:
    """
    Yields padded batches:
      x_ctx: (B, Nc, W, F)
      y_ctx: (B, Nc, 1)
      x_tgt: (B, Nt, W, F)
      y_tgt: (B, Nt, 1)
      mask_ctx: (B, Nc) True for real entries
      mask_tgt: (B, Nt) True for real entries
    """
    rng = np.random.default_rng(seed)
    n = len(X)
    episodes = []
    for start in range(0, n - episode_len + 1, stride):
        idx = np.arange(start, start + episode_len)
        episodes.append(idx)

    def pad_set(arrs: list[np.ndarray], pad_value=0.0):
        max_n = max(a.shape[0] for a in arrs)
        out, mask = [], []
        for a in arrs:
            pad_n = max_n - a.shape[0]
            if pad_n == 0:
                out.append(a)
                mask.append(np.ones((a.shape[0],), dtype=bool))
            else:
                pad_shape = (pad_n,) + a.shape[1:]
                out.append(np.concatenate([a, np.full(pad_shape, pad_value, dtype=a.dtype)], axis=0))
                mask.append(np.concatenate([np.ones((a.shape[0],), dtype=bool), np.zeros((pad_n,), dtype=bool)], axis=0))
        return np.stack(out, axis=0), np.stack(mask, axis=0)

    for i in range(0, len(episodes), batch_size):
        batch_ep = episodes[i : i + batch_size]
        if len(batch_ep) == 0:
            continue

        x_ctx_list, y_ctx_list, x_tgt_list, y_tgt_list = [], [], [], []
        for idx in batch_ep:
            perm = rng.permutation(len(idx))
            n_ctx = max(2, int(len(idx) * ctx_frac))
            ctx_idx = idx[perm[:n_ctx]]
            tgt_idx = idx[perm[n_ctx:]]
            if len(tgt_idx) == 0:
                tgt_idx = idx[perm[n_ctx - 1 : n_ctx]]

            x_ctx_list.append(X[ctx_idx])
            y_ctx_list.append(y[ctx_idx])
            x_tgt_list.append(X[tgt_idx])
            y_tgt_list.append(y[tgt_idx])

        x_ctx, m_ctx = pad_set(x_ctx_list)
        y_ctx, _ = pad_set(y_ctx_list)
        x_tgt, m_tgt = pad_set(x_tgt_list)
        y_tgt, _ = pad_set(y_tgt_list)

        yield (
            torch.from_numpy(x_ctx.astype(np.float32)),
            torch.from_numpy(y_ctx.astype(np.float32)),
            torch.from_numpy(x_tgt.astype(np.float32)),
            torch.from_numpy(y_tgt.astype(np.float32)),
            torch.from_numpy(m_ctx),
            torch.from_numpy(m_tgt),
        )


@dataclass
class VolCheckpoint:
    state_dict: dict
    y_mu: float
    y_sd: float
    feature_cols: list[str]


def train_vol_model(
    Xtr: np.ndarray, ytr: np.ndarray,
    Xva: np.ndarray, yva: np.ndarray,
    in_features: int,
    device: str,
    epochs: int = 20,
    episode_len: int = 32,
    stride: int = 8,
    ctx_frac: float = 0.5,
    batch_size: int = 16,
    lr: float = 2e-4,
    kl_weight: float = 1e-3,
) -> tuple[ANPTCN, VolCheckpoint]:
    """Train ANPTCN volatility forecasting model."""
    # Flatten y for filtering
    ytr_flat = np.asarray(ytr).reshape(-1)
    yva_flat = np.asarray(yva).reshape(-1)
    # Keep only samples with finite labels and finite features
    ok_tr = np.isfinite(ytr_flat)
    for i in range(len(ok_tr)):
        if ok_tr[i] and not np.all(np.isfinite(Xtr[i])):
            ok_tr[i] = False
    ok_va = np.isfinite(yva_flat)
    for i in range(len(ok_va)):
        if ok_va[i] and not np.all(np.isfinite(Xva[i])):
            ok_va[i] = False
    Xtr, ytr = Xtr[ok_tr], np.asarray(ytr[ok_tr]).reshape(-1, 1)
    Xva, yva = Xva[ok_va], np.asarray(yva[ok_va]).reshape(-1, 1)
    if len(Xtr) < 10:
        raise RuntimeError("After filtering non-finite samples, training set has too few samples.")
    y_mu, y_sd = zscore_fit(ytr)
    ytr_n = zscore_apply(ytr, y_mu, y_sd)
    yva_n = zscore_apply(yva, y_mu, y_sd)

    model = ANPTCN(in_features=in_features, emb_dim=64, z_dim=32).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)

    def run_epoch(Xs, ys, train: bool) -> float:
        model.train(train)
        total, steps = 0.0, 0
        loader = make_episode_batches(
            Xs, ys,
            episode_len=episode_len,
            stride=stride,
            ctx_frac=ctx_frac,
            batch_size=batch_size,
            seed=0 if train else 1,
        )
        for x_ctx, y_ctx, x_tgt, y_tgt, m_ctx, m_tgt in loader:
            x_ctx = x_ctx.to(device); y_ctx = y_ctx.to(device)
            x_tgt = x_tgt.to(device); y_tgt = y_tgt.to(device)
            m_ctx = m_ctx.to(device); m_tgt = m_tgt.to(device)

            with torch.set_grad_enabled(train):
                mu, log_sigma, (pm, plv, qm, qlv) = model(
                    x_ctx, y_ctx, x_tgt,
                    y_tgt if train else None,
                    mask_ctx=m_ctx,
                    mask_tgt=m_tgt
                )

                mt = m_tgt.float().unsqueeze(-1)
                nll = model.nll_gaussian(y_tgt, mu, log_sigma)
                nll = (nll * mt).sum() / (mt.sum().clamp_min(1.0))

                kl = torch.tensor(0.0, device=device)
                if train:
                    kl = model.kl_diag_gauss(qm, qlv, pm, plv).mean()

                loss = nll + kl_weight * kl

                if not torch.isfinite(loss):
                    continue

                if train:
                    opt.zero_grad(set_to_none=True)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()

            total += float(loss.detach().cpu())
            steps += 1

        return total / max(steps, 1) if steps > 0 else float("nan")

    best = float("inf")
    best_state = None
    for ep in range(epochs):
        tr = run_epoch(Xtr, ytr_n, train=True)
        va = run_epoch(Xva, yva_n, train=False)
        _log(f"[VOL] epoch {ep:02d} train={tr:.4f} val={va:.4f}", "white")
        if va < best:
            best = va
            best_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}

    if best_state is None:
        best_state = model.state_dict()

    model.load_state_dict(best_state)
    ckpt = VolCheckpoint(state_dict=best_state, y_mu=y_mu, y_sd=y_sd, feature_cols=[])
    return model, ckpt


@torch.no_grad()
def infer_vol_with_known_ctx_labels(
    model: ANPTCN,
    X: np.ndarray,
    y_known: np.ndarray,
    y_mu: float,
    y_sd: float,
    device: str,
    ctx_k: int = 64,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Proper inference for a trading bot:
    - At time i, you know realized vol labels up to i-1 (computed from past intraday).
    - Use those as y_ctx. Predict y at i.
    """
    model.eval()
    N, W, F = X.shape

    # Normalize y_known with training stats
    y_norm = zscore_apply(y_known, y_mu, y_sd).astype(np.float32)

    mu_out = np.full((N,), np.nan, dtype=np.float32)
    sig_out = np.full((N,), np.nan, dtype=np.float32)

    for i in range(N):
        start = max(0, i - ctx_k)
        if i - start < 4:
            continue

        x_ctx = torch.from_numpy(X[start:i]).unsqueeze(0).to(device)
        y_ctx = torch.from_numpy(y_norm[start:i]).unsqueeze(0).to(device)
        x_tgt = torch.from_numpy(X[i:i+1]).unsqueeze(0).to(device)

        m_ctx = torch.ones((1, x_ctx.shape[1]), dtype=torch.bool, device=device)
        m_tgt = torch.ones((1, 1), dtype=torch.bool, device=device)

        mu, log_sigma, _ = model(x_ctx, y_ctx, x_tgt, y_tgt=None, mask_ctx=m_ctx, mask_tgt=m_tgt)

        mu = mu.squeeze().item()
        sigma = float(torch.exp(log_sigma.squeeze()).item())

        # unnormalize
        mu_out[i] = mu * y_sd + y_mu
        sig_out[i] = sigma * y_sd

    return mu_out, sig_out


def direction_label(ret_fwd: np.ndarray, thresh: float = 0.0) -> np.ndarray:
    """Create direction labels: 1 (up), -1 (down), 0 (flat)."""
    y = np.zeros_like(ret_fwd, dtype=np.int64)
    y[ret_fwd > thresh] = 1
    y[ret_fwd < -thresh] = -1
    return y


def build_gbt_features(
    Xseq: np.ndarray,
    vol_mu: np.ndarray,
    vol_sig: np.ndarray,
) -> np.ndarray:
    """
    Tabular features for GBT:
      - last day feature vector from window
      - predicted vol mean
      - predicted vol uncertainty (sigma)
    """
    last = Xseq[:, -1, :]  # (N, F)
    f = np.concatenate([last, vol_mu[:, None], vol_sig[:, None]], axis=1)
    return f.astype(np.float32)


class Volatility:
    """Volatility forecasting strategy using TCN + ANP and GBT classifier."""
    
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.min_bars_for_training = 200  # Minimum bars needed to train (reduced for backtesting)
        self.window_w = 20
        self.delta = 1
        
    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Run the volatility forecast strategy.
        
        Args:
            symbol: str
            price: float or None (current price)
            current_time: datetime
            config: dict (strategy config from DB)
            conditions: dict (conditions from DB)
            data: dict symbol -> list of bars (historical price data up to current_time)
        
        Returns: 1 (buy), 0 (hold), -1 (sell)
        """
        if data is None:
            _log(f"[VOL] No data provided for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        
        if symbol not in data:
            _log(f"[VOL] Symbol {symbol} not found in data. Available: {list(data.keys())}", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        _log(f"[VOL] {symbol}: Received {len(bars)} bars", "cyan")
        
        # Allow config to override minimum bars
        min_bars = config.get('min_bars', self.min_bars_for_training) if config else self.min_bars_for_training
        
        if len(bars) < min_bars:
            _log(f"[VOL] {symbol}: Not enough bars ({len(bars)} < {min_bars}), returning hold", "yellow")
            return (0, None, None, "No clear signal")
        
        # Convert bars to daily data
        daily = bars_to_daily(bars)
        if daily is None:
            _log(f"[VOL] {symbol}: bars_to_daily returned None", "yellow")
            return (0, None, None, "No clear signal")
        # Need enough daily bars to create supervised samples (window_w + delta + buffer for train/val/test)
        min_daily_bars = max(30, self.window_w + self.delta + 20)  # At least 30, or window + delta + buffer
        if len(daily) < min_daily_bars:
            _log(f"[VOL] {symbol}: Not enough daily bars ({len(daily)} < {min_daily_bars}), returning hold", "yellow")
            return (0, None, None, "No clear signal")
        _log(f"[VOL] {symbol}: Converted to {len(daily)} daily bars", "cyan")
        
        # Always retrain with latest data (no caching)
        # Check config for retrain policy: "always" (default), "on_new_data", or "never"
        retrain_policy = config.get('retrain_policy', 'always') if config else 'always'
        
        should_retrain = False
        cache_key = f"{symbol}"
        
        if retrain_policy == 'always':
            _log(f"[VOL] {symbol}: Retraining with latest data (bars: {len(bars)}, daily: {len(daily)})...", "cyan")
            should_retrain = True
        elif retrain_policy == 'on_new_data':
            if cache_key not in _model_cache:
                _log(f"[VOL] {symbol}: Models not cached, training...", "cyan")
                should_retrain = True
            else:
                # Check if we have new data (more bars than last training)
                cached_info = _model_cache[cache_key]
                last_bar_count = cached_info.get("last_bar_count", 0)
                last_daily_count = cached_info.get("last_daily_count", 0)
                
                # Retrain if we have more data (at least 1 more daily bar or 5% more bars)
                if len(daily) > last_daily_count or len(bars) > last_bar_count * 1.05:
                    _log(f"[VOL] {symbol}: New data detected (bars: {len(bars)} vs {last_bar_count}, daily: {len(daily)} vs {last_daily_count}), retraining...", "cyan")
                    should_retrain = True
                else:
                    _log(f"[VOL] {symbol}: Using cached models (bars: {len(bars)}, daily: {len(daily)})", "green")
        else:  # retrain_policy == 'never'
            if cache_key not in _model_cache:
                _log(f"[VOL] {symbol}: Models not cached, training...", "cyan")
                should_retrain = True
            else:
                _log(f"[VOL] {symbol}: Using cached models (retrain_policy='never')", "green")
        
        # Train models if needed
        if should_retrain:
            try:
                self._train_models(symbol, daily, config, len(bars), len(daily))
            except Exception as e:
                _log(f"[VOL] Training error: {e}", "red")
                import traceback
                _log(traceback.format_exc(), "red")
                return (0, None, None, "No clear signal")
        # Make prediction for current time
        try:
            prediction = self._predict(symbol, daily, current_time, config)
            _log(f"[VOL] {symbol} prediction: {prediction} (1=buy, 0=hold, -1=sell)", "white")
            return prediction
        except Exception as e:
            _log(f"[VOL] Prediction error: {e}", "red")
            import traceback
            _log(traceback.format_exc(), "red")
            return (0, None, None, "No clear signal")
    def _train_models(self, symbol, daily, config, bar_count=None, daily_count=None):
        """Train volatility and directional models using full pipeline."""
        feature_cols = ["ret_1d", "absret_1d", "range_proxy", "volume"]
        
        # Work with a copy
        daily = daily.copy()
        
        # Normalize volume for features
        if 'volume' in daily.columns:
            vol_mean = daily['volume'].mean()
            if vol_mean > 0:
                daily['volume'] = daily['volume'] / vol_mean
            else:
                daily['volume'] = 0.0
        
        _log(f"Training volatility model for {symbol} with {len(daily)} daily bars", "white")
        
        # Check for NaN/inf in rv_vol before creating supervised dataset
        rv_vol_col = daily.get("rv_vol", None)
        if rv_vol_col is not None:
            nan_count = rv_vol_col.isna().sum()
            inf_count = np.isinf(rv_vol_col).sum() if hasattr(rv_vol_col, 'values') else 0
            zero_count = (rv_vol_col == 0).sum()
            if nan_count > 0 or inf_count > 0 or zero_count > 0:
                _log(f"[VOL] Warning: rv_vol has {nan_count} NaN, {inf_count} inf, {zero_count} zero values", "yellow")
        
        # Create supervised dataset for volatility forecasting
        X, y, t = make_supervised(daily, feature_cols, "rv_vol", self.window_w, self.delta)
        
        # Check for NaN/inf in y after creating supervised dataset
        if y is not None and len(y) > 0:
            y_arr = np.asarray(y).reshape(-1)
            nan_count = np.isnan(y_arr).sum()
            inf_count = np.isinf(y_arr).sum()
            if nan_count > 0 or inf_count > 0:
                _log(f"[VOL] Warning: y has {nan_count} NaN, {inf_count} inf values after make_supervised", "yellow")
        # Minimum samples: need at least window_w + delta + some buffer for train/val/test split
        # Reduced for backtesting - need at least enough for a small train/val split
        min_samples = max(20, self.window_w + self.delta + 5)  # At least 20 samples, or window + delta + small buffer
        if X is None or len(X) < min_samples:
            raise ValueError(f"Not enough samples for training: {len(X) if X is not None else 0} (need at least {min_samples}). Daily bars: {len(daily)}, window_w={self.window_w}, delta={self.delta}")
        
        # Time-based split
        t_idx = pd.DatetimeIndex(t)
        (Xtr, ytr, ttr), (Xva, yva, tva), (Xte, yte, tte) = time_split(X, y, t_idx, 0.6, 0.2)
        
        _log(f"Split: train={len(ttr)} val={len(tva)} test={len(tte)}", "white")
        
        # Train volatility model
        epochs = config.get('vol_epochs', 15)
        default_episode_len = config.get('episode_len', 32)
        stride = config.get('stride', 8)
        ctx_frac = config.get('ctx_frac', 0.5)
        batch_size = config.get('batch_size', 16)
        
        # Adjust episode_len if dataset is too small (need at least episode_len samples to create episodes)
        # Use at most 80% of available training samples for episode_len
        max_episode_len = max(8, int(len(Xtr) * 0.8))  # Minimum 8, or 80% of training samples
        episode_len = min(default_episode_len, max_episode_len)
        if episode_len < default_episode_len:
            _log(f"[VOL] Adjusted episode_len from {default_episode_len} to {episode_len} (dataset size: {len(Xtr)})", "yellow")
        
        _log(f"Training ANPTCN volatility model (epochs={epochs}, episode_len={episode_len}, train_samples={len(Xtr)})...", "white")
        try:
            vol_model, vol_ckpt = train_vol_model(
                Xtr, ytr, Xva, yva,
                in_features=len(feature_cols),
                device=self.device,
                epochs=epochs,
                episode_len=episode_len,
                stride=stride,
                ctx_frac=ctx_frac,
                batch_size=batch_size,
            )
            vol_ckpt.feature_cols = feature_cols
            _log("Volatility model training completed", "green")
        except Exception as e:
            _log(f"Volatility model training failed: {e}", "red")
            raise
        
        # Infer volatility for all samples
        _log("Inferring volatility features...", "white")
        vol_mu_all, vol_sig_all = infer_vol_with_known_ctx_labels(
            vol_model, X, y_known=y,
            y_mu=vol_ckpt.y_mu, y_sd=vol_ckpt.y_sd,
            device=self.device,
            ctx_k=config.get('ctx_k_infer', 64)
        )
        
        # Create direction labels
        daily_for_dir = daily.copy()
        daily_for_dir["ret_fwd_1d"] = np.log(daily_for_dir["close"]).diff().shift(-1)
        daily_for_dir = daily_for_dir.dropna()
        
        # Get forward returns aligned with X samples (t_idx)
        ret_fwd_values = daily_for_dir.loc[t_idx, "ret_fwd_1d"].values.astype(np.float32)
        
        # Log forward return statistics
        _log(f"[VOL] Forward return stats: mean={np.nanmean(ret_fwd_values):.4f}, std={np.nanstd(ret_fwd_values):.4f}, min={np.nanmin(ret_fwd_values):.4f}, max={np.nanmax(ret_fwd_values):.4f}", "cyan")
        
        dir_y = direction_label(ret_fwd_values, thresh=0.0)
        
        # Log label distribution before filtering
        from collections import Counter
        label_dist_before = Counter(dir_y)
        _log(f"[VOL] Direction labels before filtering: {dict(label_dist_before)}", "cyan")
        
        # Build GBT dataset, drop rows where vol inference is nan
        ok = np.isfinite(vol_mu_all) & np.isfinite(vol_sig_all)
        X2 = X[ok]
        t2 = t_idx[ok]
        dir2 = dir_y[ok]
        vmu2 = vol_mu_all[ok]
        vsig2 = vol_sig_all[ok]
        
        if len(X2) == 0:
            raise RuntimeError("Volatility inference produced no finite values")
        
        (X2tr, y2tr, t2tr), (X2va, y2va, t2va), (X2te, y2te, t2te) = time_split(
            X2, dir2[:, None], t2, 0.6, 0.2
        )
        y2tr = y2tr.squeeze(1); y2va = y2va.squeeze(1); y2te = y2te.squeeze(1)
        
        # Need matching vol arrays for these splits
        n_train = len(t2tr)
        n_val = len(t2va)
        vmu_tr, vmu_va, vmu_te = vmu2[:n_train], vmu2[n_train:n_train+n_val], vmu2[n_train+n_val:]
        vsig_tr, vsig_va, vsig_te = vsig2[:n_train], vsig2[n_train:n_train+n_val], vsig2[n_train+n_val:]
        
        Ftr = build_gbt_features(X2tr, vmu_tr, vsig_tr)
        Fva = build_gbt_features(X2va, vmu_va, vsig_va)
        Fte = build_gbt_features(X2te, vmu_te, vsig_te)
        
        _log("Training GBT directional classifier...", "white")
        
        # Log class distribution in training data
        from collections import Counter
        train_class_dist = Counter(y2tr)
        val_class_dist = Counter(y2va)
        test_class_dist = Counter(y2te)
        _log(f"[VOL] Class distribution - Train: {dict(train_class_dist)}, Val: {dict(val_class_dist)}, Test: {dict(test_class_dist)}", "cyan")
        
        # Calculate sample weights to balance classes (HistGradientBoostingClassifier doesn't support class_weight)
        from sklearn.utils.class_weight import compute_sample_weight
        sample_weights = compute_sample_weight('balanced', y=y2tr)
        _log(f"[VOL] Sample weights range: min={sample_weights.min():.3f}, max={sample_weights.max():.3f}, mean={sample_weights.mean():.3f}", "cyan")
        
        clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=400)
        clf.fit(Ftr, y2tr, sample_weight=sample_weights)
        
        from sklearn.metrics import accuracy_score, classification_report
        pred_va = clf.predict(Fva)
        pred_te = clf.predict(Fte)
        _log(f"GBT val acc: {accuracy_score(y2va, pred_va):.3f}, test acc: {accuracy_score(y2te, pred_te):.3f}", "green")
        
        # Log detailed classification report
        _log(f"[VOL] Classification report (val):\n{classification_report(y2va, pred_va, zero_division=0)}", "cyan")
        
        # Log prediction distribution
        pred_va_dist = Counter(pred_va)
        pred_te_dist = Counter(pred_te)
        _log(f"[VOL] Prediction distribution - Val: {dict(pred_va_dist)}, Test: {dict(pred_te_dist)}", "cyan")
        
        # Log class labels the classifier knows about
        if hasattr(clf, 'classes_'):
            _log(f"[VOL] Classifier classes: {clf.classes_}", "cyan")
        
        # Cache the models
        cache_key = f"{symbol}"
        _model_cache[cache_key] = {
            'vol_model': vol_model,
            'vol_ckpt': vol_ckpt,
            'clf': clf,
            'feature_cols': feature_cols,
            'last_bar_count': bar_count if bar_count is not None else 0,
            'last_daily_count': daily_count if daily_count is not None else len(daily),
        }
        _log(f"Models cached for {symbol} (bars: {bar_count}, daily: {daily_count})", "green")
    
    def _predict(self, symbol, daily, current_time, config):
        """Make prediction for current time using trained models."""
        cache_key = f"{symbol}"
        if cache_key not in _model_cache:
            return (0, None, None, "No clear signal")
        model_data = _model_cache[cache_key]
        vol_model = model_data['vol_model']
        vol_ckpt = model_data['vol_ckpt']
        clf = model_data['clf']
        feature_cols = model_data['feature_cols']
        
        # Need to create features for the current time
        # Build supervised dataset up to current time
        daily_copy = daily.copy()
        if 'volume' in daily_copy.columns:
            vol_mean = daily_copy['volume'].mean()
            if vol_mean > 0:
                daily_copy['volume'] = daily_copy['volume'] / vol_mean
        
        X, y, t = make_supervised(daily_copy, feature_cols, "rv_vol", self.window_w, self.delta)
        if X is None or len(X) == 0:
            return (0, None, None, "No clear signal")
        # Get the most recent sample (current time)
        # We need X with all samples up to current, and y_known with all RV values
        # The inference function will use context up to i-1 to predict i
        X_all = X  # All samples including current
        y_known_all = y  # All known RV values (aligned with X)
        
        # Infer volatility for current time (last sample)
        try:
            vol_mu, vol_sig = infer_vol_with_known_ctx_labels(
                vol_model, X_all, y_known_all,
                y_mu=vol_ckpt.y_mu, y_sd=vol_ckpt.y_sd,
                device=self.device,
                ctx_k=min(config.get('ctx_k_infer', 64), len(X))
            )
            
            # Get prediction for the last (current) time step
            if len(vol_mu) == 0 or not (np.isfinite(vol_mu[-1]) and np.isfinite(vol_sig[-1])):
                return (0, None, None, "No clear signal")
            vol_mu_current = vol_mu[-1]
            vol_sig_current = vol_sig[-1]
            
            # Build GBT features for current time
            X_current = X_all[-1:]  # (1, W, F)
            feat = build_gbt_features(X_current, np.array([vol_mu_current]), np.array([vol_sig_current]))[0]  # (F+2,)
            
            # Predict direction
            pred = clf.predict([feat])[0]
            
            # Log prediction details for debugging
            if hasattr(clf, 'predict_proba'):
                proba = clf.predict_proba([feat])[0]
                # Get class labels to understand probability mapping
                classes = clf.classes_ if hasattr(clf, 'classes_') else None
                proba_str = f"{dict(zip(classes, proba)) if classes is not None else proba}"
                _log(f"[VOL] {symbol} prediction: {pred} (proba: {proba_str}, features: mu={vol_mu_current:.4f}, sig={vol_sig_current:.4f})", "cyan")
            else:
                _log(f"[VOL] {symbol} prediction: {pred} (features: mu={vol_mu_current:.4f}, sig={vol_sig_current:.4f})", "cyan")
            
            return int(pred)
        except Exception as e:
            _log(f"Prediction exception: {e}", "red")
            return (0, None, None, "No clear signal")