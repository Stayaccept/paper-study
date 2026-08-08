---
uid: "concept:model-size-n"
type: "concept"
title: "模型规模 N"
aliases:
  - "参数量 N"
  - "Model size N"
sources:
  - "[[论文 - 2001.08361]]"
  - "[[论文 - 2203.15556]]"
depends_on: []
supports:
  - "[[计算最优前沿]]"
refines: []
challenges: []
limited_by:
  - "[[固定计算预算与训练条件]]"
review_status: "reviewed"
sync_status: "synced"
---

# 模型规模 $N$

## 定义

$N$ 表示用参数数量衡量的模型容量，是缩放分析中的一个资源维度。它产生于这样一个事实：更大的可训练函数族通常能逼近更复杂的语言分布，但每处理一个 token 的计算与推理成本也会随参数量增加。

## 两篇论文的口径不同

- **Kaplan：** $N$ 是 non-embedding parameters，不包含词表 embedding 与位置 embedding。这样定义后，不同深度模型的损失趋势更整齐。
- **Hoffmann：** 模型规模和 FLOPs 计算包含 embedding 矩阵；论文指出在大模型上该部分占比较小，与常用 $C\approx6ND$ 近似差异很小。

比较缩放指数或复现实验时，必须先统一这一口径。对于小模型或超大词表，embedding 是否计入可能不再可忽略。

<!-- AUTO:RELATIONS:BEGIN -->
## 关系

- 与 [[训练数据量 D]] 共同受 [[训练计算量 C]] 约束。
- 增大 $N$ 通常降低 [[交叉熵损失 L]]，但边际改善递减，且固定预算下会减少可处理的 token。
- 是 [[Kaplan 的计算最优分配]]、[[Chinchilla 的等比例扩展]] 与 [[计算最优前沿]] 的基本变量。
<!-- AUTO:RELATIONS:END -->

## 我的口径

在这里记录自己的参数统计方式；自动同步不会覆盖本节。

<!-- AUTO:EVIDENCE:BEGIN -->
## 证据

- Kaplan 的符号定义和排除 embedding 的理由：[[2001.08361/2001.08361_中文译文.pdf#page=5|中文译文 p.5]]、[[2001.08361/2001.08361_中文译文.pdf#page=6|中文译文 p.6]]、[[2001.08361/2001.08361_中文译文.pdf#page=7|中文译文 p.7]]。
- Hoffmann 的 FLOPs 与参数口径：[[2203.15556/2203.15556_中文译文.pdf#page=23|中文译文 p.23]]。
<!-- AUTO:EVIDENCE:END -->

