"""应用程序异常定义。

层次简洁，风格参照 aigenerated_images 项目的 FeishuError / ImageGenError。
"""


class AppError(Exception):
    """应用基础异常。"""

    pass


class APIError(AppError):
    """API 层可预期错误，由全局异常处理器按 status_code 返回给客户端。

    对应场景：参数错误 400、鉴权失败 401、资源不存在 404、服务内部 500。
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BusinessError(AppError):
    """业务层错误，不回 HTTP 错误，回写表格失败状态。

    GenerationService.process() 捕获后统一写 "失败: xxx" 到表格。
    """

    pass


class ConfigError(AppError):
    """配置错误，启动时失败，不可恢复。"""

    pass
