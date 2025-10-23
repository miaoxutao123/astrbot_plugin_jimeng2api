# Jimeng2API 插件

该插件让 AstrBot 可以直接调用 Jimeng（Dreamina）生成接口，复用本仓库提供的
`pyjimeng` Python 客户端，并提供 `/jimeng` 指令组。

## 指令一览

- `/jimeng status`：查看本地服务状态以及远程 session 健康。
- `/jimeng start`、`/jimeng stop`：启动或停止本地 Jimeng 包装服务。
- `/jimeng points`：查询当前 session 的积分情况。
- `/jimeng image <提示词> [key=value ...]`：文生图，支持附加参数覆盖默认配置。
- `/jimeng compose <url[,url...]> <提示词> [key=value ...]`：基于已有图片再创作。
- `/jimeng video <提示词> [key=value ...]`：文生视频。
- `/jimeng auto on|off`：设置插件加载时是否自动启动服务。
- `/jimeng session set|add|remove|list|clear`：管理 session token 列表。

可选参数包括 `model=`, `ratio=`, `resolution=`, `response=`, `negative=`,
`sample=`, `width=`, `height=` 以及 `session=` 等，用于覆盖配置文件中的默认值。

## LLM 工具

以下函数已注册为 LLM Tool，可在多轮对话中直接调用：

- `jimeng_image`：文生图。
- `jimeng_image_compose`：图生图。
- `jimeng_video`：文生视频。
- `jimeng_points`：查询积分信息。

## 配置说明

`_conf_schema.json` 定义了仪表盘可调整的配置项，包括：

- session token 列表与自动启动开关；
- 文生图默认模型、比例、分辨率、返回格式与负面提示词；
- 文生图默认采样强度；
- 文生视频默认模型、分辨率与输出格式等。

可以在 AstrBot 仪表盘中修改这些配置，或直接编辑生成的
`data/config/jimeng2api_config.json` 文件。
