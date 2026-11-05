"""TagForge —— 一个 Mp3tag 风格的音频标签管理器。

分层：
    fields       统一字段模型（canonical schema），屏蔽各格式键名差异
    adapters     格式适配器：ID3 / Vorbis / MP4 / APEv2 四类写入引擎
    naming       文件名 <-> 标签 模板引擎
    library      扫描、批量写入、事务快照与回滚
    gui          桌面端界面（Tkinter），只依赖以上四层
"""

__version__ = "1.0.0"
