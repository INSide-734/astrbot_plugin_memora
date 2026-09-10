# Dashboard 共享库模块上下文

面包屑：[`pages/dashboard/AGENTS.md`](../../AGENTS.md) → `src/lib/`

## 边界

- `bridge.ts`：唯一页面 API 边界；调用 `/astrbot_plugin_memora/page/*`，清理敏感字段并统一 envelope/错误处理。
- `config.ts`、`configSections.ts`：配置默认值、分区和 revision 同步；必须与 Pydantic 模型及 `_conf_schema.json` 对齐。
- `i18n.ts`：zh/en/ru 文案 key；更新 key 必须同步 hook、mock、页面测试和 smoke。
- `navigation.ts`：Hash 路由、五组导航和页面标题；未知 hash 回退 graph。
- `globalSearch.ts`、`utils.ts`、`constants.ts`：纯函数和跨页面协议，不得产生副作用或复制 API 逻辑。

## 安全与数据契约

bridge 输入必须经过现有 sanitize helper；不得记录 query、prompt、记忆正文、ID 列表、身份或 Provider 密钥。分页、筛选、selection 和写回使用服务端真实响应，不得伪造客户端 total/offset。配置和实体写回必须传 revision，冲突返回显式 envelope。

## 验证

```powershell
Set-Location pages/dashboard
npm test -- --run src/lib
npm run build
```

改动 bridge/config/i18n 时还需运行对应后端契约测试与 `python scripts/check_all.py`。
