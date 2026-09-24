# 票08 · 依赖修正与决策日志

## What to build
1. `pyproject.toml`:diar extra 的 `pyannote.audio>=3.3` → `>=4.0`(community-1 管线实际要求,其 config.yaml 声明依赖 4.0.0;.venv-diar 已装 4.0.7,纯声明对齐,不触发安装)。
2. `README.md` 决策日志:按仓库既有格式追加声纹认人一条(路线:复用分人自带 embedding 薄层自研;关键裁定:档案向量=样本直抽、一套参考音+use 标记、校准双条件、只预填不代提交;出处:docs/research/04 + docs/superpowers/specs/20260925-声纹自动认人-spec.md)。

## 验收标准
- [ ] pyproject 语法有效(python -c "import tomllib;tomllib.load(...)" 或 pip check 等价轻校验)
- [ ] README 决策日志条目格式与既有条目一致
- [ ] 全量 pytest 零回归

## Blocked by
无,可立即开始。

## 涉及路径
- pyproject.toml(改)
- README.md(改)

## 副作用声明
- 默认只跑类型检查/单文件测试;不安装依赖

## decision_refs
D16、D17

## review_blocks
无
