# DCG Schema Builder — HTTP 契约（v1，冻结于 2026-11-05）

本地工具：粘贴原始数据 → 推断结构树 → 拖拽生成 DCG schema → 一键编译并跑数据。
后端只用 Python 标准库，前端只用原生 HTML/CSS/JS（无构建步骤）。

* 监听：`127.0.0.1:<port>`，默认 `8765`
* 编码：请求/响应均为 `application/json; charset=utf-8`
* 业务错误一律 **HTTP 200** + `{"ok": false, ...}`；只有 HTTP 层错误（非法 JSON 体、超大请求）才用 4xx。
* 所有响应都必须含 `ok: boolean`。

---

## GET `/`

返回 `web/index.html`。其余静态资源按 `web/` 目录同路径返回（`/app.js`、`/style.css`）。

## GET `/api/health`

```json
{"ok": true, "dcg_version": "2.2.2", "python": "3.13.3", "port": 8765}
```

## GET `/api/demo`

返回一份可直接上手的示例（前端「载入示例」按钮用）。

```json
{"ok": true, "text": "{\n  \"orders\": [...]\n}", "hint": "dict 包一层 list，schema 的 input.source 会自动填 $.orders"}
```

## POST `/api/infer`

请求：

```json
{"text": "<原始 JSON 文本>"}
```

响应（成功）：

```json
{
  "ok": true,
  "mode": "rows",                 // "rows" | "wrapper" | "single"
  "source": null,                 // wrapper 时为 "$.orders"，否则 null
  "row_count": 3,
  "schema_name": "orders",        // 建议的 schema 名（可被前端覆盖）
  "tree": {
    "label": "rows[3]", "path": "", "type": "rows", "count": 3,
    "sample": null, "children": [ <node>, ... ]
  },
  "fields": [
    {"path": "$.0", "label": "[0]", "suggest": "col_0", "type": "str",
     "samples": ["A-1", "B-2"], "depth": 1}
  ],
  "warnings": ["键 a.b 含 '.'，DCG 路径无法表达，已跳过"]
}
```

`node` 结构（递归）：

```json
{"label": "qty", "path": "$.qty", "type": "int64", "count": null,
 "sample": 2, "children": null}
```

* `type` ∈ `rows | object | list | int64 | float64 | str | bool | any`（与 `dcg.schema.TYPES` 对齐；对象/行容器用 `object`/`rows` 仅用于展示）。
* 叶子字段同时出现在 `fields`（扁平、有序、供拖拽），`fields[].path` 是**相对行根**的 DCG 取值路径（如 `$.0`、`$.data.ID`）。
* `mode`：
  * `rows` —— 样本是数组，每项是一行；
  * `wrapper` —— 样本是对象且只有一个 list 值，`source` 给出 `"$.<key>"`，行取该 list；
  * `single` —— 样本是对象（无 list），视为单行。
* `fields[].suggest` 为建议输出名（`[0]` → `col_0`，`a.b` → `a_b`，保留合法标识符字符）。
* 非法 JSON：`{"ok": false, "stage": "json", "error": "第 3 行第 5 列: Expecting ',' delimiter"}`。

## POST `/api/run`

请求：

```json
{"text": "<原始 JSON 文本>", "schema": {"name": "...", "output": {"shape": "list"}, "out": {...}}}
```

`schema.output.shape` 与 DCG 一致，四选一：`list`（默认，行列表）| `dict`（列式 `{字段: [值]}`）|
`index`（`{行号: 行}`）| `rows`（行式 `[{字段: 值}]`）；见 `dcg/schema.py` 的 `SHAPES`。不接受 `object`。

响应（成功；`shape="list"` / `"rows"`）：

```json
{
  "ok": true,
  "out": [[...], [...]],          // 原始结果；行数超过 200 时只留前 200 行
  "out_count": 3,                 // 结果总行数（不是返回条数）
  "truncated": false,
  "code": "def run(_d):\n ...",
  "elapsed_ms": 1.28,
  "manifest": {"name": "order", "lines": 8, "chars": 118,
               "schema_sha1": "df257bd7...", "generator_version": "2.2.2.g1"},
  "warnings": []
}
```

响应（成功；`shape="dict"` / `"index"`）：`out` 保持该 shape 的原生形状 ——
`dict` 每列只保留前 200 个值；`index` 只保留按行号数值排序的前 200 个键。`out_count` 仍是总行数，
`truncated` 表示是否发生过截断。

响应（失败）：

```json
{"ok": false, "stage": "schema", "error": "out.fields.x: 类型必须是 ...", "code": null}
```

* `stage` ∈ `json | schema | generate | run`：分别对应 JSON 解析、`dcg.validate`、`dcg.generate`、exec+`run()`。
* `stage=run` 时应同时返回 `code`（已生成的源码），方便用户定位。
* `manifest` 缺字段时前端要能容错显示（取不到就显示 `—`）。

## 实现分工（写域，勿越界）

| 文件 | 负责人 |
| --- | --- |
| `tools/app.py`、`tools/API.md`、`tools/README.md`、`tools/run.cmd` | Lead |
| `tools/dcg_bridge.py`、`tools/tests/` | `bridge-core` |
| `tools/web/index.html`、`web/style.css`、`web/app.js` | `web-ui` |

`dcg_bridge` 对外函数（`app.py` 只依赖这三个）：

```python
def dcg_version() -> str: ...
def infer(text: str) -> dict: ...
def run(schema: dict, text: str) -> dict: ...
```

`infer` 返回上面的成功/失败体；`run` 同理。异常不得穿透到 `app.py`（除 `MemoryError`），一律转成 `{"ok": false, ...}`。
