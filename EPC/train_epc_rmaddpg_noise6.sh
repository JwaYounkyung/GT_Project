#!/bin/sh

CUDA_VISIBLE_DEVICES="" python -m maddpg_o.experiments.train_epc1 \
    --initial-population=6 \
    --num-selection=3 \
    --num-stages=2 \
    --stage-num-episodes 100000 50000 \
    --num-good=3 \
    --num-food=3 \
    --good-policy=r-att-maddpg \
    --save-dir="./result/epc_rmaddpg_noise6_pop6" \
    --save-rate=1000 \
    --train-rate=1000 \
    --n-cpu-per-agent=40 \
    --noise-std=6.0 \
    2>&1 | tee epc_rmaddpg_noise6_pop6.log
