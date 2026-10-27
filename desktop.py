"""Desktop overlay launcher using pywebview"""
import webview


def create_overlay_window():
    """Create a transparent, always-on-top overlay window for Live2D display"""
    return webview.create_window(
        title='AI Auto Streamer',
        url='http://127.0.0.1:8000/?overlay=1',
        width=800,
        height=600,
        transparent=True,
        frameless=True,
        on_top=True,
        resizable=True,
        easy_drag=False,
    )


def create_control_window():
    """Create a control panel window"""
    return webview.create_window(
        title='AI Auto Streamer - 控制台',
        url='http://127.0.0.1:8000/control',
        width=650,
        height=800,
        on_top=True,
        resizable=True,
    )


def main():
    print("启动 AI Auto Streamer 桌面覆盖层...")
    print("请确保服务器已运行: python main.py")
    print()

    create_overlay_window()
    create_control_window()

    webview.start(debug=False)


if __name__ == '__main__':
    main()
