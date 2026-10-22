package app.pet;

import com.sun.jna.platform.win32.User32;
import com.sun.jna.platform.win32.WinDef.HWND;

/**
 * Windows 侧的窗口控制：点击穿透 + 不抢焦点 + 不进任务栏。
 *
 * <p>JavaFX 没有这些能力，必须走 JNA 调 Win32。
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

    private static final int SWP_NOSIZE = 0x0001;
    private static final int SWP_NOMOVE = 0x0002;
    private static final int SWP_NOZORDER = 0x0004;
    private static final int SWP_FRAMECHANGED = 0x0020;

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

    /**
     * 设置点击穿透。
     *
     * @param through true = 鼠标点不到这个窗口（穿透到底下的窗口）
     */
    public static void setClickThrough(HWND hwnd, boolean through) {
        if (hwnd == null) {
            return;
        }
        try {
            int ex = User32.INSTANCE.GetWindowLong(hwnd, GWL_EXSTYLE);
            if (through) {
                ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
            } else {
                ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW);
            }
            User32.INSTANCE.SetWindowLong(hwnd, GWL_EXSTYLE, ex);
            User32.INSTANCE.SetWindowPos(hwnd, null, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED);
        } catch (Throwable t) {
            // 非 Windows 平台或权限不足时静默降级：桌宠本身不依赖这个特性
        }
    }
}
