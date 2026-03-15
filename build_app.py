import webview
URL = 'http://127.0.0.1:5000' # Если есть Pinggy или Render - вставь сюда ту ссылку!
webview.create_window('Nexus Chat', URL, width=1200, height=800)
webview.start()
