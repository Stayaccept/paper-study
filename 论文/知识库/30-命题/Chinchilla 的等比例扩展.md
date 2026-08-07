---
uid: "claim:chinchilla-balanced-scaling"
type: "claim"
title: "Chinchilla 的等比例扩展"
aliases:
  - "Chinchilla 0.5/0.5"
  - "参数与数据等比例扩展"
sources:
  - "[[论文 - 2203.15556]]"
depends_on:
  - "[[模型规模 N]]"
  - "[[训练数据量 D]]"
  - "[[训练计算量 C]]"
  - "[[交叉熵损失 L]]"
  - "[[计算最优前沿]]"
supports:
  - "[[固定训练计算量下如何分配参数与数据]]"
refines:
  - "[[Kaplan 的计算最优分配]]"
challenges: []
limited_by:
  - "[[固定计算预算与训练条件]]"
  - "[[外推范围与语料分布]]"
review_status: "reviewed"
sync_status: "synced"
---

# Chinchilla 的等比例扩展

## 命题

对于 Hoffmann et al. (2022) 研究的稠密 autoregressive Transformer，在固定训练 FLOPs 下达到最低训练损失时，最优参数量和训练 token 数随预算近似等比例扩大：

$$
N_{opt}\propto C^a,\qquad D_{opt}\propto C^b,\qquad a\approx b\approx 0.5.
$$

因为 $C\approx 6ND$，$a+b\approx1$；“等比例”指 $N,D$ 对 $C$ 的指数近似相等，并不是说参数数值与 token 数值相等。

## 三种估计

| 方法 | $a$ | $b$ |
|---|---:|---:|
| 训练曲线包络 | 0.50 | 0.50 |
| IsoFLOP 剖面 | 0.49 | 0.51 |
| 参数化损失拟合 | 0.46 | 0.54 |

三种不同方法接近，说明结论不是单一拟合形式的偶然产物；但差异本身也表明 (0.5) 不是精确常数。

<!-- AUTO:RELATIONS:BEGIN -->
## 关系

- `refines` [[Kaplan 的计算最优分配]]：同意“不应把模型训练至最低可能损失”，修正计算增加时 $N,D$ 的分配比例。
- 由 [[较小模型配更多数据可优于更大欠训练模型]] `supports`。
- 回答 [[固定训练计算量下如何分配参数与数据]]，其方法解释见 [[Kaplan 与 Chinchilla 为什么得到不同结论]]。
<!-- AUTO:RELATIONS:END -->

## 适用提醒

主分析运行在少于一个 epoch 的区域；高预算外推出现前沿曲率，且数据质量、模型架构与优化器改变都可能移动最优点。实际项目还需把推理成本纳入决策：若预训练性能相当，更小的模型通常具有额外部署收益。

## 我的批注

这里记录你将该规律映射到具体训练预算时使用的假设。

<!-- AUTO:EVIDENCE:BEGIN -->
## 证据

- 方法 1 与方法 2 的指数：[[2203.15556/2203.15556_中文译文.pdf#page=6|中文译文 p.6]]。
- 参数化损失和解析最优解：[[2203.15556/2203.15556_中文译文.pdf#page=7|中文译文 p.7]]。
- 三种方法汇总与置信区间：[[2203.15556/2203.15556_中文译文.pdf#page=8|中文译文 p.8]]。
- 高预算曲率与多 epoch 限制：[[2203.15556/2203.15556_中文译文.pdf#page=14|中文译文 p.14]]、[[2203.15556/2203.15556_中文译文.pdf#page=22|中文译文 p.22]]。
<!-- AUTO:EVIDENCE:END -->
