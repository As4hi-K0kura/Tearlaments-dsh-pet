package app.pet;

import com.sun.jna.Pointer;
import com.sun.jna.platform.win32.User32;
import com.sun.jna.platform.win32.WinDef.DWORD;
import com.sun.jna.platform.win32.WinDef.HWND;

/**
 * Windows 侧的窗口控制：进任务栏/抢焦点/点击穿透。
 *
 * <p>JavaFX 没有这些能力，必须走 JNA 调 Win32。
 *
 * <p><b>桌宠是「常驻在桌面上的一层 UI」，不是应用窗口</b>，所以有两条常驻样式：
 * <ul>
 *   <li>{@code WS_EX_TOOLWINDOW} —— 任务栏里<b>没有</b>它的按钮，Alt-Tab 列表里也没有它。
 *       不设的话，JVM 进程会在任务栏留一个按钮，图标是 JavaFX 的默认通用图标（很丑）。</li>
 *   <li>{@code WS_EX_NOACTIVATE} —— 点它也<b>不抢</b>前台焦点，用户正在打字时不会被打断。</li>
 * </ul>
 * 这两条与「点击穿透」是<b>正交</b>的：穿不穿透都不影响它不进任务栏。
 * （早期版本把 TOOLWINDOW 和穿透绑在一起，导致关掉穿透时任务栏图标又弹回来。）
 *
 * <p>注意「点击穿透」与「悬停检测」互斥：开了 {@code WS_EX_TRANSPARENT} 之后
 * 鼠标事件不再送达这个窗口，桌宠就收不到点击/拖拽。所以这里做成可切换的开关，
 * 而不是常开。
 */
public final class Win32 {

    private static final int GWL_EXSTYLE = -20;
    private static final int WS_EX_TRANSPARENT = 0x00000020;
    private static final int WS_EX_LAYERED = 0x00080000;
    private static final int WS_EX_TOOLWINDOW = 0x00000080;
    private static final int WS_EX_NOACTIVATE = 0x08000000;
    private static final int WS_EX_APPWINDOW = 0x00040000;

    private static final int SWP_NOSIZE = 0x0001;
    private static final int SWP_NOMOVE = 0x0002;
    private static final int SWP_NOZORDER = 0x0004;
    private static final int SWP_FRAMECHANGED = 0x0020;

    /** z-order 里取「下一个（更下面的）窗口」。 */
    private static final int GW_HWNDNEXT = 2;
    /** 虚拟键码：鼠标左键。 */
    private static final int VK_LBUTTON = 0x01;
    /** GetAsyncKeyState 的「当前按下」标志位。 */
    private static final int KEY_DOWN_MASK = 0x8000;
    /** HWND_TOPMOST = (HWND)-1 —— JNA 的 WinUser 里没有这个常量，自己造一个。 */
    private static final HWND HWND_TOPMOST = new HWND(Pointer.createConstant(-1));

    private Win32() {
    }

    /** 按窗口标题找 HWND（JavaFX 没有公开的取原生句柄 API，这是最稳的折中）。 */
    public static HWND findByTitle(String title) {
        try {
            return User32.INSTANCE.FindWindow(null, title);
        } catch (Throwable t) {
            return null;
        }
    }

    /** 当前前台窗口（show 桌宠之前记下来，show 完还回去）。 */
    public static HWND getForegroundWindow() {
        try {
            return User32.INSTANCE.GetForegroundWindow();
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * 把前台焦点还给某个窗口。
     *
     * <p>Windows 限制跨进程抢焦点，但「当前就是前台进程的调用方」是被允许的 ——
     * 桌宠刚 show 完正好符合这个条件，所以能顺利把焦点还给 DSH。
     */
    public static void setForegroundWindow(HWND hwnd) {
        if (hwnd == null) {
            return;
        }
        try {
            User32.INSTANCE.SetForegroundWindow(hwnd);
        } catch (Throwable t) {
            // 拿不回来就算了，不影响桌宠本身
        }
    }

    /**
     * 应用桌宠的窗口样式。
     *
     * @param through true = 鼠标点不到这个窗口（穿透到底下的窗口）
     */
    public static void applyWindowStyle(HWND hwnd, boolean through) {
        if (hwnd == null) {
            return;
        }
        try {
            int ex = User32.INSTANCE.GetWindowLong(hwnd, GWL_EXSTYLE);
            // ① 常驻不进任务栏 / Alt-Tab
            ex |= WS_EX_TOOLWINDOW;
            ex &= ~WS_EX_APPWINDOW;
            // ② 常驻不抢焦点
            ex |= WS_EX_NOACTIVATE;
            // ③ 分层窗口（透明背景必需）
            ex |= WS_EX_LAYERED;
            // ④ 只有穿透模式才加 TRANSPARENT
            if (through) {
                ex |= WS_EX_TRANSPARENT;
            } else {
                ex &= ~WS_EX_TRANSPARENT;
            }
            User32.INSTANCE.SetWindowLong(hwnd, GWL_EXSTYLE, ex);
            User32.INSTANCE.SetWindowPos(hwnd, null, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED);
        } catch (Throwable t) {
            // 非 Windows 平台或权限不足时静默降级：桌宠本身不依赖这个特性
        }
    }

    /**
     * 设置点击穿透。
     *
     * @param through true = 鼠标点不到这个窗口（穿透到底下的窗口）
     */
    public static void setClickThrough(HWND hwnd, boolean through) {
        applyWindowStyle(hwnd, through);
    }

    /**
     * 只在样式跑偏时补一次（{@link #applyWindowStyle} 的幂等版本）。
     *
     * <p>供「样式纠偏定时器」调用：Windows 偶尔会把扩展样式重置回去，
     * 一旦 {@code WS_EX_TOOLWINDOW} 掉了，任务栏上就会重新冒出那个通用图标按钮。
     * 先比对再决定要不要刷，避免定时 SetWindowPos 引起闪烁。
     *
     * @return true = 发生过修正
     */
    public static boolean ensureWindowStyle(HWND hwnd, boolean through) {
        if (hwnd == null) {
            return false;
        }
        try {
            int ex = User32.INSTANCE.GetWindowLong(hwnd, GWL_EXSTYLE);
            int want = (ex & ~WS_EX_TRANSPARENT) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED;
            if (through) {
                want |= WS_EX_TRANSPARENT;
            }
            want &= ~WS_EX_APPWINDOW;
            if (want == ex) {
                return false;
            }
            applyWindowStyle(hwnd, through);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * 重新声明「置顶」。
     *
     * <p><b>为什么必须周期性重做</b>：桌宠是 GDI 分层窗口（WS_EX_LAYERED），
     * 而 DSH 这类 Electron 应用是 DirectComposition 直绘窗口（WS_EX_NOREDIRECTIONBITMAP）。
     * DWM 合成时<b>直绘表面盖过分层窗口，不看 z-order</b>；而且任何一次
     * 「别的窗口被激活/恢复」都会把置顶带抢走 —— 桌宠随即被压到下面、整个消失。
     *
     * <p>所以光有 {@code WS_EX_TOPMOST} 这个样式位不够，必须真的调
     * {@code SetWindowPos(HWND_TOPMOST)} 把自己重新塞回置顶带。
     * 不带 {@code SWP_ACTIVATE}，所以不会抢焦点。
     */
    public static void setAlwaysOnTop(HWND hwnd) {
        if (hwnd == null) {
            return;
        }
        try {
            User32.INSTANCE.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE);
        } catch (Throwable t) {
            // 忽略
        }
    }

    /**
     * 鼠标左键当前是否按着（**系统真实状态**，不是 JavaFX 事件推出来的）。
     *
     * <p>为什么需要：拖拽时如果 release 事件丢了（鼠标在窗口外松开、或被别的程序/系统菜单
     * 抢走输入），JavaFX 那边永远收不到「松手」，桌宠会一直卡在 dragging 跟着光标满屏漂。
     * JavaFX 21 没有公开的按键状态 API，所以直接读 Windows 的 {@code GetAsyncKeyState}。
     */
    public static boolean isLeftMouseDown() {
        try {
            return (User32.INSTANCE.GetAsyncKeyState(VK_LBUTTON) & KEY_DOWN_MASK) != 0;
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * 桌宠是否被压到了前台窗口下面。
     *
     * <p>做法：从前台窗口开始，沿 z-order 往下走，看能不能走到桌宠。
     * 能走到 = 桌宠在它下面 = 被盖住了。
     */
    public static boolean isBehindForeground(HWND hwnd) {
        if (hwnd == null) {
            return false;
        }
        try {
            HWND fg = User32.INSTANCE.GetForegroundWindow();
            if (fg == null || fg.equals(hwnd)) {
                return false;
            }
            HWND w = fg;
            for (int i = 0; i < 128 && w != null; i++) {
                w = User32.INSTANCE.GetWindow(w, new DWORD(GW_HWNDNEXT));
                if (w == null) {
                    break;
                }
                if (w.equals(hwnd)) {
                    return true;
                }
            }
            return false;
        } catch (Throwable t) {
            return false;
        }
    }
}
