"""PyInstaller 打包入口：双击 exe 即启动本地服务并打开浏览器。

必须走 web.main()，否则 --host/--port/--no-browser 参数不生效。
"""

import sys

from tender_extractor.web import main

if __name__ == "__main__":
    sys.exit(main())
