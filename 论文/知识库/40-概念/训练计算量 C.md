---
uid: "concept:training-compute-c"
type: "concept"
title: "训练计算量 C"
aliases:
  - "训练 FLOPs C"
  - "Training compute C"
sources:
  - "[[论文 - 2001.08361]]"
  - "[[论文 - 2203.15556]]"
depends_on:
  - "[[模型规模 N]]"
  - "[[训练数据量 D]]"
supports:
  - "[[计算最优前沿]]"
refines: []
challenges: []
limited_by:
  - "[[固定计算预算与训练条件]]"
review_status: "reviewed"
sync_status: "synced"
---

# 训练计算量 $C$

## 定义

$C$ 是完成预训练所使用的浮点运算量。对标准稠密 Transformer，一阶近似为：

\[
C\approx 6ND=6NBS,
\]

其中 $N$ 是参数量，$D$ 是已处理 token 数，$B$ 是每步 batch token 数，$S$ 是更新步数。系数 6 近似包含一次前向和两倍前向成本的反向传播。

这个近似揭示了问题结构：当 $C$ 固定，$N$ 与 $D$ 的乘积基本固定，因而必须寻找两者之间的最佳权衡。

## 不同计算口径

- **实际 $C$：** 某次训练真正使用的估计 FLOPs。
- **Kaplan 的 $C_{min}$：** 对 batch-size 低效进行校正后，达到目标损失所需的最少 non-embedding 计算量。
- **Hoffmann 的 $C$：** 计入全部参数与 embedding 相关 FLOPs，并用更细的 Transformer 运算表核算；论文验证它与 $6ND$ 近似非常接近。

这些口径只有在定义统一时才能直接比较。

<!-- AUTO:RELATIONS:BEGIN -->
## 关系

- 约束 [[模型规模 N]] 与 [[训练数据量 D]] 的联合选择。
- [[固定训练计算量下如何分配参数与数据]] 以 $C$ 为约束，[[交叉熵损失 L]] 为目标。
- 可行配置中损失最低的集合构成 [[计算最优前沿]]。
<!-- AUTO:RELATIONS:END -->

## 我的口径

在这里记录是否计入 embedding、注意力上下文项、激活重算及硬件利用率；自动同步不会覆盖本节。

<!-- AUTO:EVIDENCE:BEGIN -->
## 证据

- Kaplan 的 $C\approx6NBS$、PF-day 与 $C_{min}$ 定义：[[2001.08361/2001.08361_中文译文.pdf#page=5|中文译文 p.5]]、[[2001.08361/2001.08361_中文译文.pdf#page=6|中文译文 p.6]]。
- Hoffmann 的约束 $FLOPs(N,D)=C$：[[2203.15556/2203.15556_中文译文.pdf#page=4|中文译文 p.4]]。
- Hoffmann 的细项 FLOPs 核算及与 $6ND$ 的比较：[[2203.15556/2203.15556_中文译文.pdf#page=23|中文译文 p.23]]。
<!-- AUTO:EVIDENCE:END -->

