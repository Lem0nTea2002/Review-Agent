# 第三方组件说明

## Alibaba OpenCodeReview

- 包：`@alibaba-group/open-code-review`
- 固定版本：`1.8.5`
- 许可证：Apache License 2.0
- 上游：https://github.com/alibaba/open-code-review
- 用途：作为 Review Agent 可替换的仓库级代码审阅后端

Review Agent 通过受控 CLI Adapter 调用该组件，统一报告中的第三方 Finding 始终标记
`source="open-code-review"`。第三方实现、名称和许可证归其原作者所有。
