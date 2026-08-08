---
uid: "concept:training-data-d"
type: "concept"
title: "训练数据量 D"
aliases:
  - "训练 token 数 D"
  - "Training data D"
sources:
  - "[[论文 - 2001.08361]]"
  - "[[论文 - 2203.15556]]"
depends_on: []
supports:
  - "[[计算最优前沿]]"
refines: []
challenges: []
limited_by:
  - "[[外推范围与语料分布]]"
review_status: "reviewed"
sync_status: "synced"
---

# 训练数据量 $D$

## 定义

$D$ 以 token 计量训练数据，但至少有三种不同口径：

1. **dataset size：** 数据集中不同位置所包含的 token 总量；
2. **tokens seen：** 一次训练中模型实际处理的 token 数；
3. **unique tokens：** 去重后独立内容的近似数量。

在少于一个 epoch 时，前两者可以接近；发生重复采样后，tokens seen 可以继续增长，而独立信息量不会等比例增长。

## 为什么需要这个变量

模型必须通过样本约束其参数。固定 $N$ 时，更多高质量且新增的信息通常会降低 [[交叉熵损失 L]]；固定 [[训练计算量 C]] 时，增大 $D$ 又会迫使 $N$ 下降。因此 $D$ 不是背景条件，而是 [[计算最优前沿]] 的主动决策变量。

<!-- AUTO:RELATIONS:BEGIN -->
## 关系

- 与 [[模型规模 N]] 的乘积一阶决定 [[训练计算量 C]]。
- [[Kaplan 的计算最优分配]] 将最优 $D$ 估计为约 $C^{0.27}$；[[Chinchilla 的等比例扩展]] 修正为约 $C^{0.5}$。
- [[训练数据重复收益递减]] 和 [[外推范围与语料分布]] 限制“更多 token”可以带来的收益。
<!-- AUTO:RELATIONS:END -->

## 我的口径

在这里记录语料总量、去重后规模、采样权重与训练轮数；自动同步不会覆盖本节。

<!-- AUTO:EVIDENCE:BEGIN -->
## 证据

- Kaplan 对 $D$ 的定义和有限数据损失：[[2001.08361/2001.08361_中文译文.pdf#page=5|中文译文 p.5]]、[[2001.08361/2001.08361_中文译文.pdf#page=8|中文译文 p.8]]。
- Hoffmann 将 $D$ 定义为训练 token 并建立固定预算问题：[[2203.15556/2203.15556_中文译文.pdf#page=4|中文译文 p.4]]。
- 不同子集的重复轮数说明 tokens seen 与唯一数据不同：[[2203.15556/2203.15556_中文译文.pdf#page=17|中文译文 p.17]]。
<!-- AUTO:EVIDENCE:END -->

