---
uid: "claim:kaplan-compute-optimal-allocation"
type: "claim"
title: "Kaplan 的计算最优分配"
aliases:
  - "Kaplan 0.73/0.27"
sources:
  - "[[论文 - 2001.08361]]"
depends_on:
  - "[[模型规模 N]]"
  - "[[训练数据量 D]]"
  - "[[训练计算量 C]]"
  - "[[交叉熵损失 L]]"
  - "[[计算最优前沿]]"
supports:
  - "[[固定训练计算量下如何分配参数与数据]]"
refines: []
challenges: []
limited_by:
  - "[[固定计算预算与训练条件]]"
  - "[[外推范围与语料分布]]"
review_status: "reviewed"
sync_status: "synced"
---

# Kaplan 的计算最优分配

## 命题

在 Kaplan et al. (2020) 的 WebText2、模型族、critical-batch 校正与损失拟合下，计算最优配置随预算增长约为：

\[
N_{opt}\propto C_{min}^{0.73},\quad
D_{opt}\propto C_{min}^{0.27},\quad
B_{crit}\propto C_{min}^{0.24},\quad
S_{min}\propto C_{min}^{0.03}.
\]

因此，增加十倍训练计算量时，最优模型规模约增大五倍，而处理的数据量约增大不足两倍；高效训练应使用更大的模型，并在距离完全收敛尚远时停止。

## 这条命题解决什么问题

它首次把“训练更大的模型还是训练更久”变成了带指数的资源配置规则。这里的核心贡献是提出可计算的 [[计算最优前沿]]；具体 $0.73/0.27$ 则是随后可以被更强实验重新估计的量。

<!-- AUTO:RELATIONS:BEGIN -->
## 关系

- `supports` [[固定训练计算量下如何分配参数与数据]] 的早期答案。
- 被 [[Chinchilla 的等比例扩展]] `refines`：Hoffmann 保留前沿与 early-stopping 思路，但把最优指数修正为接近等比例。
- 不把后续修正写成这条命题对 Chinchilla 的 `challenges`；语义方向应由新证据指向旧命题。
<!-- AUTO:RELATIONS:END -->

## 适用提醒

$C_{min}$ 是经过 batch-size 效率校正的 non-embedding 计算量；$N$ 也不含 embedding 参数。把公式直接套到其他模型架构、超长上下文或远超实验区间的预算，会改变定义或扩大外推误差。

## 我的批注

这里保留给人工判断：你的训练配置是否满足本文的计算与数据假设。

<!-- AUTO:EVIDENCE:BEGIN -->
## 证据

- 总结公式及指数：[[2001.08361/2001.08361_中文译文.pdf#page=5|中文译文 p.5]]。
- 最优模型规模与步数的经验结果：[[2001.08361/2001.08361_中文译文.pdf#page=13|中文译文 p.13]]、[[2001.08361/2001.08361_中文译文.pdf#page=14|中文译文 p.14]]。
- $N,B,S,D$ 指数汇总：[[2001.08361/2001.08361_中文译文.pdf#page=17|中文译文 p.17]]。
- 对远外推、小数据和计算近似的注意事项：[[2001.08361/2001.08361_中文译文.pdf#page=19|中文译文 p.19]]。
<!-- AUTO:EVIDENCE:END -->
