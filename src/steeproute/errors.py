"""SteeprouteError exception hierarchy. Per Architecture §Category 10."""


class SteeprouteError(Exception):
    """Base class. Never raised directly."""


class PreExecutionError(SteeprouteError):
    """Maps to exit code 2. Raised when the tool cannot produce any output."""

    user_message: str
    detail: str | None

    def __init__(self, user_message: str, detail: str | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class BadCLIArgError(PreExecutionError):
    """Malformed or out-of-range CLI argument."""


class CacheNotFoundError(PreExecutionError):
    """FR24 coverage miss: query area not contained in any prepared entry."""


class CacheCorruptedError(PreExecutionError):
    """manifest OK but graph.pkl unreadable."""


class DataSourceUnavailableError(PreExecutionError):
    """steeproute-setup: Overpass/IGN down or unreachable."""


class DEMCoverageError(PreExecutionError):
    """A graph vertex falls outside the DEM raster's coverage (or lands on a nodata cell)."""


class PipelineContractError(PreExecutionError):
    """An inter-stage invariant in the setup-side pipeline orchestrator was violated."""


class SolverError(PreExecutionError):
    """Unexpected solver-internal failure - best-so-far may be empty; treat as pre-exec tier."""
