from astrbot.api import logger
from astrbot.api.star import Context, Star


class BilibiliPlugin(Star):
    """Bilibili 私信适配器插件入口。

    导入 bilibili_adapter 模块即触发平台适配器注册；
    适配器实例的创建与生命周期由 AstrBot 平台管理器负责，
    插件热重载时核心会自动注销本插件注册的适配器。
    """

    def __init__(self, context: Context):
        super().__init__(context)
        try:
            from . import bilibili_adapter  # noqa: F401  导入即注册适配器
        except ImportError as e:
            logger.error(f"导入 Bilibili Adapter 失败，请检查依赖是否安装: {e}")
            # 抛出异常，避免插件处于“已加载但不可用”的不一致状态
            raise
