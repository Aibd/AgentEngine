# Import agent modules at application boot to register them.
# Side-effects: each submodule's @register_agent decorator runs here, so any
# caller that does `import agents` ends up with a fully populated registry.
from . import deep_research  # noqa: F401
from . import general_chat  # noqa: F401
