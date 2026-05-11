# INTELLISTOCK_SCHEMA: {"strategy": "Candles", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"n_candles": 5, "ema_period": 50, "rsi_period": 14}}
# INTELLISTOCK_DESCRIPTION: Candles v5. Geometric patterns (Engulfing, Hammers, Stars), EMA trend filter, RSI momentum filter, volatility-adjusted.
# DIFFICULTY: 9
"""
Candles Strategy v5 - Context Aware & Geometric Pattern Recognition.

Improvements over v3/v4:
1. Training Data: Replaced 'random tendencies' with explicit geometric definitions 
   of known high-probability patterns (Engulfing, Hammers, Stars).
2. Trend Filter: Uses EMA(50) to filter out counter-trend signals.
3. Momentum Filter: Uses RSI to prevent buying at tops or selling at bottoms.
4. Volatility Adjustment: Normalizes candle sizes to recent volatility.

Returns: 1 = buy, 0 = hold, -1 = sell.
"""

from __future__ import annotations

import os
import numpy as np

# Pre-trained model weights saved here at Docker build time by pretrain_candles.py
PRETRAINED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'candles_v5.pt')

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="CandlesStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[CandlesStrategy] {msg}")

# --- Configuration ---
N_CANDLES = 5
FEATURES_PER_CANDLE = 4

LABEL_HOLD = 0
LABEL_BUY = 1
LABEL_SELL = 2
NUM_CLASSES = 3

CONFIDENCE_THRESHOLD = 0.60  # Higher threshold because patterns are distinct
EMA_PERIOD = 50
RSI_PERIOD = 14

# --- Helper Math Functions ---

def _calculate_ema(values, period):
    """Calculate EMA via TA-Lib. Returns last value (scalar) or None if insufficient data."""
    if len(values) < period:
        return None
    import talib
    arr = np.asarray(values, dtype=np.float64)
    result = talib.EMA(arr, timeperiod=period)
    # Return last non-NaN value
    valid = result[~np.isnan(result)]
    return float(valid[-1]) if len(valid) > 0 else None

def _calculate_rsi(closes, period=14):
    """Calculate RSI via TA-Lib. Returns last value (scalar), or 50.0 if insufficient data."""
    if len(closes) < period + 1:
        return 50.0
    import talib
    arr = np.asarray(closes, dtype=np.float64)
    result = talib.RSI(arr, timeperiod=period)
    valid = result[~np.isnan(result)]
    return float(valid[-1]) if len(valid) > 0 else 50.0

# --- Feature Extraction ---

def _candle_features(bars, idx):
    """
    Extract ONLY the 4 core candlestick features.
    Returns: (body_ratio, upper_wick, lower_wick, is_bullish)
    """
    if idx < 0 or idx >= len(bars):
        return (0.5, 0.25, 0.25, 0.5)
    
    b = bars[idx]
    o = float(b.get("o") or b.get("c") or 0)
    h = float(b.get("h") or b.get("c") or 0)
    l = float(b.get("l") or b.get("c") or 0)
    c = float(b.get("c") or 0)
    
    # Normalize by range to make features price-agnostic
    eps = 1e-9
    total_range = float(h - l) + eps
    body = abs(float(c - o))
    
    body_ratio = min(1.0, body / total_range)
    upper_wick = (float(h) - max(o, c)) / total_range
    lower_wick = (min(o, c) - float(l)) / total_range
    is_bullish = 1.0 if c >= o else 0.0
    
    return (body_ratio, upper_wick, lower_wick, is_bullish)

def _bars_to_sequence(bars):
    """Convert bars to model input."""
    if len(bars) < N_CANDLES:
        return None
    take_start = len(bars) - N_CANDLES
    feats = []
    for i in range(N_CANDLES):
        feats.append(_candle_features(bars, take_start + i))
    return np.array(feats, dtype=np.float32).reshape(1, N_CANDLES, FEATURES_PER_CANDLE)

# --- Geometric Pattern Generation (The Fix for Synthetic Data) ---

def _generate_specific_pattern(label, noise=0.1):
    """
    Generates SPECIFIC textbook patterns rather than random noise.
    This teaches the model to recognize actual trading setups.
    """
    # Initialize basic 5-candle sequence (mostly small bodies, mixed wicks)
    # Shape: [N_CANDLES, 4] -> (body, upper, lower, bullish)
    pattern = np.zeros((N_CANDLES, 4), dtype=np.float32)
    
    # Fill background noise (small consolidation candles)
    for i in range(N_CANDLES):
        pattern[i, 0] = np.random.uniform(0.2, 0.5) # Medium body
        pattern[i, 1] = np.random.uniform(0.1, 0.3) # Small wicks
        pattern[i, 2] = np.random.uniform(0.1, 0.3)
        pattern[i, 3] = 1.0 if np.random.random() > 0.5 else 0.0

    if label == LABEL_BUY:
        setup_type = np.random.choice(['engulfing', 'hammer', 'morning_star'])
        
        if setup_type == 'engulfing':
            # Bearish candle, then massive Bullish engulfing
            # 2nd to last candle (Bearish)
            pattern[-2, 0] = 0.4
            pattern[-2, 3] = 0.0 
            # Last candle (Bullish Engulfing)
            pattern[-1, 0] = 0.9 # Big body
            pattern[-1, 1] = 0.05
            pattern[-1, 2] = 0.05
            pattern[-1, 3] = 1.0
            
        elif setup_type == 'hammer':
            # Downtrend then Hammer
            pattern[-2, 3] = 0.0 # Bearish prev
            # Hammer
            pattern[-1, 0] = 0.15 # Tiny body
            pattern[-1, 1] = 0.05 # Tiny upper
            pattern[-1, 2] = 0.8  # Long lower wick
            pattern[-1, 3] = 1.0  # Bullish close preferred
            
        elif setup_type == 'morning_star':
            # Big Bear, Small Star, Big Bull
            pattern[-3, 0] = 0.8; pattern[-3, 3] = 0.0 # Big Red
            pattern[-2, 0] = 0.1; pattern[-2, 3] = 0.0 # Small Doji
            pattern[-1, 0] = 0.8; pattern[-1, 3] = 1.0 # Big Green

    elif label == LABEL_SELL:
        setup_type = np.random.choice(['engulfing', 'shooting_star', 'evening_star'])
        
        if setup_type == 'engulfing':
            # Bullish then Massive Bearish
            pattern[-2, 0] = 0.4; pattern[-2, 3] = 1.0
            pattern[-1, 0] = 0.9; pattern[-1, 3] = 0.0 # Big Red
            
        elif setup_type == 'shooting_star':
            # Uptrend then Shooting Star
            pattern[-2, 3] = 1.0
            pattern[-1, 0] = 0.15
            pattern[-1, 1] = 0.8 # Long upper wick
            pattern[-1, 2] = 0.05
            pattern[-1, 3] = 0.0 # Bearish close
            
        elif setup_type == 'evening_star':
            pattern[-3, 0] = 0.8; pattern[-3, 3] = 1.0 # Big Green
            pattern[-2, 0] = 0.1; pattern[-2, 3] = 1.0 # Small Doji
            pattern[-1, 0] = 0.8; pattern[-1, 3] = 0.0 # Big Red

    else: # HOLD
        # Random chop, dojis, or extremely small candles
        for i in range(N_CANDLES):
            pattern[i, 0] = np.random.uniform(0.0, 0.3) # Very small bodies
            pattern[i, 3] = 1.0 if np.random.random() > 0.5 else 0.0

    # Apply Noise
    noise_val = np.random.normal(0, noise, pattern.shape)
    pattern[:, :3] += noise_val[:, :3]
    pattern = np.clip(pattern, 0, 1)
    
    # Recalculate geometric constraints (Body + Wicks must approx 1.0)
    for i in range(len(pattern)):
        total = pattern[i,0] + pattern[i,1] + pattern[i,2]
        if total > 1.0:
            pattern[i, :3] /= total
            
    return pattern

def _build_training_data(n_samples=3000, seed=None):
    """Build dataset using geometric patterns. seed: optional int for reproducibility."""
    if seed is not None:
        np.random.seed(seed)
    X_list = []
    y_list = []
    
    # Class balance: More Holds than actions
    counts = {LABEL_HOLD: 1500, LABEL_BUY: 750, LABEL_SELL: 750}
    
    for label, count in counts.items():
        for _ in range(count):
            pat = _generate_specific_pattern(label)
            X_list.append(pat)
            y_list.append(label)
            
    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int64)
    
    # Shuffle
    perm = np.random.permutation(len(X))
    return X[perm], y[perm]

# --- Model Definition (Standard LSTM) ---

def _make_model():
    """Construct and return an untrained CandleLSTM."""
    import torch.nn as nn
    class CandleLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(FEATURES_PER_CANDLE, 64, batch_first=True, num_layers=2, dropout=0.2)
            self.fc = nn.Sequential(
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(32, NUM_CLASSES)
            )
        def forward(self, x):
            _, (h_n, _) = self.lstm(x)
            return self.fc(h_n[-1])
    return CandleLSTM()


def _build_and_train_model(seed=42):
    import torch
    import torch.nn as nn
    from torch.utils.data import TensorDataset, DataLoader

    # Reproducibility: same seed => same training data and model init
    if seed is not None:
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    device = torch.device("cpu")
    model = _make_model().to(device)
    X, y = _build_training_data(seed=seed)
    
    X_t = torch.from_numpy(X)
    y_t = torch.from_numpy(y)
    
    dataset = TensorDataset(X_t, y_t)
    # Deterministic shuffle when seed is set
    g = torch.Generator()
    if seed is not None:
        g.manual_seed(seed)
    loader = DataLoader(dataset, batch_size=64, shuffle=True, generator=g)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    
    n_epochs = 25
    _log(f"[Candles] Training geometric pattern model ({n_epochs} epochs, {len(dataset)} samples)...", "cyan")
    model.train()
    for epoch in range(n_epochs):
        total_loss = 0
        n_batches = 0
        for bx, by in loader:
            optimizer.zero_grad()
            out = model(bx)
            loss = criterion(out, by)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1
        avg_loss = total_loss / max(n_batches, 1)
        if epoch % 5 == 0 or epoch == n_epochs - 1:
            _log(f"[Candles]   Epoch {epoch+1}/{n_epochs} — avg loss: {avg_loss:.4f}", "cyan")

    model.eval()
    return model


def _get_or_train_model(seed=42):
    """Load the pre-trained model from disk if available; otherwise train from scratch."""
    import torch
    if os.path.exists(PRETRAINED_PATH):
        try:
            model = _make_model()
            model.load_state_dict(torch.load(PRETRAINED_PATH, map_location='cpu', weights_only=True))
            model.eval()
            _log("[Candles] Loaded pretrained model from disk (skipping training).", "cyan")
            return model
        except Exception as e:
            _log(f"[Candles] Pretrained model load failed ({e}), retraining...", "yellow")
    return _build_and_train_model(seed=seed)


# --- Strategy Class ---

class Candles:
    def __init__(self):
        self.min_bars = max(N_CANDLES, EMA_PERIOD + 1)

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        if data is None or symbol not in data:
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if len(bars) < self.min_bars:
            return (0, None, None, "No clear signal")
        # 1. Model Inference (Pattern Recognition)
        cache = strategy_cache if strategy_cache is not None else {}
        if "model_v5" not in cache:
            seed = None
            if isinstance(config, dict) and "seed" in config:
                try:
                    seed = int(config["seed"])
                except (TypeError, ValueError):
                    pass
            if seed is None:
                seed = 42  # Default fixed seed for reproducibility
            cache["model_v5"] = _get_or_train_model(seed=seed)
        
        model = cache["model_v5"]
        seq = _bars_to_sequence(bars)
        if seq is None: 
            return (0, None, None, "No clear signal")
        import torch
        import torch.nn.functional as F
        
        with torch.no_grad():
            logits = model(torch.from_numpy(seq))
            probs = F.softmax(logits, dim=1)[0]
            max_prob = probs.max().item()
            pred = logits.argmax(dim=1).item()

        # 2. Context Calculation (Trend & Momentum)
        closes = [float(b.get('c', 0)) for b in bars]
        
        ema_long = _calculate_ema(closes, EMA_PERIOD)
        rsi = _calculate_rsi(closes, RSI_PERIOD)
        
        current_price = closes[-1]
        
        # Trend Determination
        # Uptrend if price is above EMA 50
        is_uptrend = current_price > ema_long if ema_long else False
        is_downtrend = current_price < ema_long if ema_long else False
        
        # 3. Decision Logic with Filters
        
        # PATTERN: BUY
        if pred == LABEL_BUY and max_prob > CONFIDENCE_THRESHOLD:
            # FILTER 1: Trend Filter (Don't buy in downtrend unless deep oversold)
            if not is_uptrend and rsi > 30:
                _log(f"{symbol}: Buy pattern ignored (Downtrend + not oversold RSI={rsi:.1f})", "white")
                return (0, None, None, "No clear signal")
            # FILTER 2: RSI Filter (Don't buy at the top)
            if rsi > 70:
                _log(f"{symbol}: Buy pattern ignored (Overbought RSI={rsi:.1f})", "white")
                return (0, None, None, "No clear signal")
            _log(f"{symbol}: **BUY** (Pattern Conf {max_prob:.2f} | Trend OK | RSI {rsi:.1f})", "green")
            return (1, None, None, "Buy signal generated")
        # PATTERN: SELL
        elif pred == LABEL_SELL and max_prob > CONFIDENCE_THRESHOLD:
            # FILTER 1: Trend Filter (Don't short in uptrend unless deep overbought)
            if not is_downtrend and rsi < 70:
                _log(f"{symbol}: Sell pattern ignored (Uptrend + not overbought RSI={rsi:.1f})", "white")
                return (0, None, None, "No clear signal")
            # FILTER 2: RSI Filter (Don't sell at the bottom)
            if rsi < 30:
                _log(f"{symbol}: Sell pattern ignored (Oversold RSI={rsi:.1f})", "white")
                return (0, None, None, "No clear signal")
            _log(f"{symbol}: **SELL** (Pattern Conf {max_prob:.2f} | Trend OK | RSI {rsi:.1f})", "yellow")
            return (-1, None, None, "Sell signal generated")
        return (0, None, None, "No clear signal")