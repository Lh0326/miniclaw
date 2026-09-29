class MiniClawError(Exception):
    code = "miniclaw_error"
    retryable = False
    side_effects_possible = False


class ApplicationConfigurationError(MiniClawError):
    code = "application_configuration_error"


class ModelError(MiniClawError):
    code = "model_error"


class AuthenticationError(ModelError):
    code = "model_authentication_error"


class RateLimitError(ModelError):
    code = "model_rate_limit"
    retryable = True


class ModelProtocolError(ModelError):
    code = "model_protocol_error"


class ModelUnavailableError(ModelError):
    code = "model_unavailable"
    retryable = True


class InvariantViolation(MiniClawError):
    code = "run_invariant_violation"


class ToolError(MiniClawError):
    code = "tool_error"


class ToolNotFound(ToolError):
    code = "tool_not_found"


class ToolArgumentsInvalid(ToolError):
    code = "tool_arguments_invalid"


class ToolSchemaError(ToolError):
    code = "tool_schema_error"
