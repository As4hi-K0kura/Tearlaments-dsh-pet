# dsh-pet-status

把 DSH 的**会话事件**聚合成桌宠用的「工作状态档位」，并按 `dsh-pet` 的契约对外提供本地 HTTP 接口。

> 这是 **DSH 插件（bundle）**，装进 DSH 后在**宿主进程内**运行：直接 `ctx.on('session/event', …)` 拿实时事件，
> 不必去读 `~/.dsh/sessions` 落盘文件，也不必自己解压 zstd。

---

## 一、在 DSH 里直接安装

### 方式 A：应用内「插件」页（推荐，UI 路径）

1. 打开 DSH → 左侧边栏 **「插件」**
2. 点 **「添加插件」**
3. 在 **「包名或地址」** 输入框里填本插件目录的**绝对路径**（DSH 不接受相对路径）：

   ```
   <本仓库的绝对路径>\desktop-pet\tools\dsh-plugin
   ```

   例如仓库克隆在 `D:\code\Tearlaments-dsh-pet` 时填 `D:\code\Tearlaments-dsh-pet\desktop-pet\tools\dsh-plugin`。

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

## 二、装上以后有什么

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

## 三、进程托管：装上插件，桌宠自动出现

本插件同时兼任桌宠的**进程宿主**，把「起桌宠」也收进插件生命周期：

- **装上插件** → 桌宠自动拉起并接上 3080；
- **卸载插件** → 桌宠自动收掉，不留孤儿进程；
- **桌宠崩了** → 按退避自动重拉。

> 桌宠本体仍然是独立的 JavaFX 程序，插件只是负责它的生死，**没有改动 Java 侧任何代码**。

### 3.1 启动配置

复制 `pet-launch.example.json` 成 `pet-launch.json`，按本机实际路径改：

```json
{
  "command": [
    "C:/path/to/jdk-21/bin/java.exe",
    "-Dstdout.encoding=UTF-8",
    "--module-path",
    "C:/path/to/javafx-base-21.0.4-win.jar;C:/path/to/javafx-graphics-21.0.4-win.jar;C:/path/to/javafx-controls-21.0.4-win.jar",
    "--add-modules",
    "javafx.controls",
    "-cp",
    "C:/path/to/desktop-pet/target/classes;C:/path/to/gson-2.11.0.jar;C:/path/to/jna-5.14.0.jar;C:/path/to/jna-platform-5.14.0.jar",
    "app.pet.PetApp",
    "--dsh"
  ],
  "cwd": "C:/path/to/desktop-pet",
  "autoRestart": true,
  "restartDelayMs": 3000
}
```

`command` 是**数组**（每个元素一个参数），不要自己加引号 —— 子进程直接按数组 spawn，不走 shell。
Windows 上 classpath / module-path 里的分号是**字面量**，照写即可。

配置来源按优先级取第一个有效的：

1. `cordis.patch.yml` 里的 `config.launch`（DSH 原生配置方式）
2. 插件目录下的 `pet-launch.json`
3. 环境变量 `DSH_PET_LAUNCH`（JSON 字符串）

其他开关：

| 环境变量 | 作用 |
| --- | --- |
| `DSH_PET_LAUNCH_FILE` | 改用别的路径读启动配置（自测隔离用） |
| `DSH_PET_STATUS_DISABLE_SUPERVISOR=1` | 整体关闭托管，只保留「报档位」 |

### 3.2 三种运行模式

插件启动时按下面的顺序判定，判定结果在 `/debug` 里能看到：

| 模式 | 触发条件 | 插件对桌宠生死的态度 |
| --- | --- | --- |
| `adopted` | 锁文件里的 pid 还活着 | **接管**：改写归属，不重复拉起 |
| `external` | 宽限期 5s 内发现有**活连接** | **不碰**：不接管生死，只用看门狗盯着 |
| `spawned` | 宽限期 5s 内没有任何活连接 | **自己拉起**，并负责收尾 |

判据是**活连接**（`/state` 长轮询的未决请求数 > 0），不是时间戳 ——
时间戳分不清「18 秒前还活着」和「18 秒前就已经死了」。

> `external` 模式下有个看门狗：连续 30s 没有活连接，就判定那个桌宠已经退出 → 转成 `spawned` 由插件接手拉起。
> 桌宠是 8s 轮询一次，30s 足够宽松，不会误判。

### 3.3 归属：锁文件

归属写在 `~/.dsh-pet/pet.lock.json`：

```json
{ "pid": 17712, "owner": "dsh-pet-status:23328:1792721275653", "startedAt": 1792721275653 }
```

`owner` 是「插件实例」的身份（宿主 pid + 启动时刻）。换手时（插件热重载）新实例会改写 `owner`；
旧实例 `dispose()` 时发现 `owner` 已经不属于自己，就**不杀**桌宠 —— 否则热重载会把桌宠误杀。

### 3.4 看诊断

```
GET http://127.0.0.1:3080/dsh-pet-7340/debug
```

返回里会多出一段 `supervisor`：

```json
{
  "supervisor": {
    "mode": "spawned",
    "childPid": 17712,
    "owns": true,
    "source": "pet-launch.json",
    "command": ["...java.exe", "..."],
    "autoRestart": true
  },
  "petSeenAgeMs": 812,
  "openStateRequests": 1
}
```

---

## 四、卸载

在 DSH「插件」页里卸载，或命令行：

```bat
"%LOCALAPPDATA%\Programs\DSH NEXT\DSH NEXT.exe" --expose-internals ^
  "%LOCALAPPDATA%\Programs\DSH NEXT\resources\app.asar\lib\plugin-cli.js" ^
  desktop remove dsh-pet-status
```

卸载时插件会收掉**归它管的**桌宠进程并清掉锁文件。

---

## 五、自测

```bat
node test.plugin.mjs
node test.supervisor.mjs
```

- `test.plugin.mjs`：用假的 `ctx` 跑一遍事件聚合与路由，11 项断言。
- `test.supervisor.mjs`：跑一遍托管的四种场景（无配置 / 外部桌宠 / 自己拉起 / 接管），15 项断言。
  它用 `DSH_PET_LAUNCH_FILE` + 假的 `USERPROFILE` 隔离，不会碰到你真实的桌宠。

（端口冲突时用 `DSH_PET_STATUS_PORT=3085` 换端口。）

---

## 六、包结构说明

| 文件 | 作用 |
| --- | --- |
| `index.js` | 插件主体：`export function apply(ctx)`，含档位聚合、HTTP 服务、托管接线 |
| `pet-supervisor.js` | 进程托管：配置解析、锁文件、三模式判定、宽限期、看门狗、拉起/收尾 |
| `pet-launch.json` | 本机启动配置（**不入库**，见 `.gitignore`） |
| `pet-launch.example.json` | 启动配置样例 |
| `test.plugin.mjs` `test.supervisor.mjs` | 自测脚本 |
| `cordis.patch.yml` | bundle patch：把插件行插进 DSH 配置树（`id` 必须与包名一致，且该行不能写行尾注释） |
| `assets/icon.svg` | 插件页图标（相对 `package.json` 的路径，≤256 KiB） |
| `locale/en.json` `locale/zh.json` | 插件页显示名与描述（`meta.title` / `meta.description`） |
| `package.json` | 清单：`dsh.bundle.patch` 声明这是 bundle，`engines.dsh` 声明兼容的 DSH 版本 |

`engines.dsh` 走的是 DSH 安装前的版本闸门：写错会在 `pnpm` 跑之前就被拒（`incompatible-version`），不会下载任何东西。
