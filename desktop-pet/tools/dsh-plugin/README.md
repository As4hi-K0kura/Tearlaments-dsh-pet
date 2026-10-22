# dsh-pet-status

把 DSH 的**会话事件**聚合成桌宠用的「工作状态档位」，并按 `dsh-pet` 的契约对外提供本地 HTTP 接口。

> 这是 **DSH 插件（bundle）**，装进 DSH 后在**宿主进程内**运行：直接 `ctx.on('session/event', …)` 拿实时事件，
> 不必去读 `~/.dsh/sessions` 落盘文件，也不必自己解压 zstd。

---

## 一、在 DSH 里直接安装

### 方式 A：应用内「插件」页（推荐，UI 路径）

1. 打开 DSH → 左侧边栏 **「插件」**
2. 点 **「添加插件」**
3. 在 **「包名或地址」** 输入框里填本插件目录的**绝对路径**：

   ```
   <本仓库>/desktop-pet/tools/dsh-plugin
   ```

4. 确认安装。DSH 会执行 `pnpm add <路径>`，把本包登记成 profile 的组合包并挂进配置树。

安装完成后**立即生效**（桌面 profile 支持热加载，不必重启 DSH）；
插件页会显示本插件的图标、名称与描述，并可在「已安装插件」里单独启停它的组件。

> 走本地目录时 pnpm 会把它记成 `link:` 依赖（Windows 上是 Junction），
> 所以**改本目录下的源码即时生效**，不用重装。

### 方式 B：压缩包（要分发给别人时）

```bat
npm pack          # 在本目录执行，产出 dsh-pet-status-1.0.0.tgz
```

然后在同一个「包名或地址」输入框里填 tarball 的绝对路径：

```
D:\path\to\dsh-pet-status-1.0.0.tgz
```

### 方式 C：npm 包名（发布到 npm 之后）

在输入框里填 `dsh-pet-status`（或发布时改用的 scoped 名）。

### 方式 D：命令行一条命令装上

```bat
install.bat
```

（等价于 `DSH NEXT.exe --expose-internals …\lib\plugin-cli.js desktop add <本目录绝对路径>`，
和 UI 里「添加插件」走的是同一套安装逻辑。）

---

## 二、装完就能用

装上以后，DSH 宿主进程内会起一个**只监听 127.0.0.1** 的 HTTP 服务：

| 路由 | 方法 | 说明 |
| --- | --- | --- |
| `/dsh-pet-7340/state` | GET | 长轮询取当前档位（`?since=<counter>&timeout=<ms>` 成对必填，≤10000ms） |
| `/dsh-pet-7340/pose/report` | POST | 渲染端上报实况 `{x,y,facing}` |
| `/dsh-pet-7340/work-status` | POST | 外部槽直接写档位（与 DSH 会话按序合并） |
| `/dsh-pet-7340/debug` | GET | 调试快照（各会话档位、事件序号、上报记录） |

默认端口 **3080**（被占用则顺延到 3081…）；可用环境变量 `DSH_PET_STATUS_PORT` 指定。

> 为什么不挂 DSH 自己的 webServer（60060）：桌面版有「浏览器访问」闸门，
> 关闭时**任何**路径都回 `403 Browser access is disabled`，跨进程客户端读不到。
> 自起 127.0.0.1 端口不受该闸门影响，且 3080 正好落在桌宠客户端 `--dsh` 的自动发现范围（3080~3104）内。

### 档位与优先级

| 档位 | 含义 | 优先级 |
| --- | --- | --- |
| `waiting` | 等用户批准 / 等回答 | 60 |
| `error` | 出错 | 50 |
| `working` | 正在干活（工具调用） | 40 |
| `thinking` | 正在思考 | 30 |
| `result` | 工具返回结果（过渡态） | 25 |
| `success` | 本轮完成 | 20 |

**这张表是宿主侧的合并表**：跨会话取优先级最高的那条，同级取最近更新的；
客户端只跟随合并结果，不再自己比一次 rank。

---

## 三、卸载

在 DSH「插件」页里卸载，或命令行：

```bat
"%LOCALAPPDATA%\Programs\DSH NEXT\DSH NEXT.exe" --expose-internals ^
  "%LOCALAPPDATA%\Programs\DSH NEXT\resources\app.asar\lib\plugin-cli.js" ^
  desktop remove dsh-pet-status
```

---

## 四、自测

```bat
node test.plugin.mjs
```

用假的 `ctx` 跑一遍事件聚合与路由，11 项断言。
（端口冲突时用 `DSH_PET_STATUS_PORT=3085` 换端口。）

---

## 五、包结构说明

| 文件 | 作用 |
| --- | --- |
| `index.js` | 插件主体：`export function apply(ctx)` |
| `cordis.patch.yml` | bundle patch：把插件行插进 DSH 配置树（`id` 必须与包名一致，且该行不能写行尾注释） |
| `assets/icon.svg` | 插件页图标（相对 `package.json` 的路径，≤256 KiB） |
| `locale/en.json` `locale/zh.json` | 插件页显示名与描述（`meta.title` / `meta.description`） |
| `package.json` | 清单：`dsh.bundle.patch` 声明这是 bundle，`engines.dsh` 声明兼容的 DSH 版本 |

`engines.dsh` 走的是 DSH 安装前的版本闸门：写错会在 `pnpm` 跑之前就被拒（`incompatible-version`），不会下载任何东西。
