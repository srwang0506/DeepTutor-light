"""Versioned application contracts; existing domain services still own execution."""

from typing import Literal

from pydantic import Field

from .models import Command


class Locator(Command):
    blob_hash: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    page: int | None = Field(default=None, ge=1)
    region: tuple[float, float, float, float] | None = None
    quote: str = Field(default="", max_length=16000)
    path: str = Field(default="", max_length=500)
    line: int | None = Field(default=None, ge=1)


class ObjectRef(Command):
    owner: Literal["current_account"] = "current_account"
    scope: str = Field(max_length=200)
    kind: str = Field(min_length=1, max_length=60)
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    locator: Locator = Field(default_factory=Locator)


class ActionRequest(Command):
    action: Literal["practice_from_selection"]
    target: ObjectRef
    allowed_sources: list[ObjectRef] = Field(default_factory=list, max_length=20)
    network_policy: Literal["offline", "model_only"] = "model_only"
    permissions: list[Literal["read_source", "create_practice"]] = Field(
        default_factory=lambda: ["read_source", "create_practice"]
    )
    idempotency_key: str = Field(min_length=16, max_length=100)
    max_output_tokens: int = Field(default=2500, ge=1000, le=4000)
    timeout_seconds: int = Field(default=150, ge=30, le=180)


class ActionReceipt(Command):
    operation_id: str
    committed: bool
    state: Literal["running", "prepared", "committed", "failed"]
    results: list[ObjectRef] = Field(default_factory=list)
    pending_sync: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    cost: float | None = None
    elapsed_seconds: float | None = None
    continue_actions: list[str] = Field(default_factory=list)


class ConnectorManifest(Command):
    id: str
    name: str
    mode: Literal["native", "authorized_read", "file_exchange", "external_open", "reviewable_write"]
    capabilities: list[str]
    formats: list[str]
    authorization: str
    cursor: str
    deletion: str
    conflicts: str
    quota: str
    degraded: str


def connector_manifests():
    rows = [
        (
            "studio",
            "学习与科研原生对象",
            "native",
            ["read_revision", "practice_from_selection", "evidence", "compare", "review_patch"],
            ["PDF", "TeX", "JSON"],
            "当前账户和项目权限",
            "稳定对象游标",
            "保留历史版本",
            "版本冲突阻止覆盖",
            "每次测验最多 4,000 输出 token",
            "模型不可用时保留选区和操作回执",
        ),
        (
            "ssh",
            "SSH 只读工作区",
            "authorized_read",
            ["list", "read_file", "repository_metadata"],
            ["text", "CSV", "JSON"],
            "本机 SSH 别名和允许目录",
            "文件快照哈希",
            "不删除远端数据",
            "新快照保留旧版",
            "有界读取与超时",
            "无法安全判断的工作树状态显示未知",
        ),
        (
            "github",
            "GitHub 仓库",
            "authorized_read",
            ["tree", "blob", "commit"],
            ["Git", "text"],
            "已有 GitHub 授权",
            "commit SHA",
            "不删除远端数据",
            "按提交冻结",
            "遵守 API 限流",
            "授权失败保留已有快照",
        ),
        (
            "tracking",
            "MLflow / W&B / TensorBoard 导出",
            "file_exchange",
            ["import_run_snapshot"],
            ["JSON", "CSV"],
            "用户选择的本地文件",
            "外部运行 ID 与快照",
            "不修改来源",
            "相同操作键不同内容拒绝",
            "每批最多 500 个运行",
            "导出声明不等于已验证执行",
        ),
        (
            "bibliography",
            "Zotero / 文献工具文件交换",
            "file_exchange",
            ["import", "export"],
            ["BibTeX", "RIS", "CSL JSON"],
            "用户选择的文件",
            "DOI / arXiv / 原始条目",
            "不删除来源库",
            "精确标识冲突需要审核",
            "每批最多 1,000 条",
            "保留原记录，报告格式转换损失",
        ),
        (
            "discovery",
            "Crossref / OpenAlex / arXiv",
            "authorized_read",
            ["search", "references"],
            ["JSON", "Atom"],
            "公开 API，按服务需要配置凭证",
            "提供方游标或有界排序分页",
            "本地保留检索快照",
            "发行版本不按标题自动合并",
            "退避与覆盖范围可见",
            "失败、零结果、未授权分别显示",
        ),
    ]
    keys = list(ConnectorManifest.model_fields)
    return [ConnectorManifest(**dict(zip(keys, row))).model_dump() for row in rows]
