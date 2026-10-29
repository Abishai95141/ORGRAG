from app.connectors.base import Connector
from app.connectors.github import GitHubConnector
from app.connectors.upload import UploadConnector
from app.connectors.web import WebConnector

CONNECTORS: dict[str, type[Connector]] = {
    c.kind: c for c in (UploadConnector, WebConnector, GitHubConnector)
}


def get_connector_class(kind: str) -> type[Connector]:
    try:
        return CONNECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown connector kind: {kind}") from exc
