#!/usr/bin/env python3
"""
Pre-trains the CandlesStrategy LSTM model and saves weights to disk.
Run once at Docker image build time so backtests skip the ~30s training step.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from candles import _build_and_train_model, PRETRAINED_PATH
import torch

print(f"[pretrain_candles] Training CandlesStrategy model (seed=42)...")
model = _build_and_train_model(seed=42)

os.makedirs(os.path.dirname(PRETRAINED_PATH) or '.', exist_ok=True)
torch.save(model.state_dict(), PRETRAINED_PATH)
print(f"[pretrain_candles] Saved → {PRETRAINED_PATH}")
