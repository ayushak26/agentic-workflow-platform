"""Importing this package triggers @NodeRegistry.register decorators
on every node module. Keep this list in sync as new node types are added."""
from . import _stubs           # noqa: F401  (LiteralNode, EchoNode)
from . import transform        # noqa: F401  (TransformAgent)
from . import rag              # noqa: F401  (RAGAgent)
from . import router           # noqa: F401  (RouterAgent)
from . import human_in_loop    # noqa: F401  (HumanInLoopAgent)