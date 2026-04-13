"""
Error hierarchy for job_finder.

All custom exceptions inherit from JobFinderError.
See Appendix J of the implementation plan for usage patterns.
"""


class JobFinderError(Exception):
    """Base exception for all job_finder errors."""
    pass


class PIILeakError(JobFinderError):
    """PII detected in output destined for remote LLM or log. CRITICAL.

    This should halt all processing immediately. Never catch and continue.
    """
    pass


class LLMParseError(JobFinderError):
    """LLM returned non-JSON or invalid schema. Retryable.

    The LLM router will retry up to `retry_on_parse_failure` times
    before raising this to the caller.
    """
    def __init__(self, message: str, raw_response: str | None = None):
        super().__init__(message)
        self.raw_response = raw_response


class SelectorResolutionError(JobFinderError):
    """All selector strategies failed for a form field. Needs human intervention.

    Attributes:
        field_name: The field that couldn't be resolved.
        strategies_tried: List of strategies attempted.
    """
    def __init__(self, message: str, field_name: str = "", strategies_tried: list = None):
        super().__init__(message)
        self.field_name = field_name
        self.strategies_tried = strategies_tried or []


class ATSFormError(JobFinderError):
    """ATS form structure is unexpected. Should be logged to failures.db.

    Raised when the form layout doesn't match templates or replay traces
    and the Form Interpreter cannot generate a reliable fill plan.
    """
    pass


class AccountError(JobFinderError):
    """Account creation or login failed. May require human intervention.

    Covers scenarios like: failed signup, locked account, unexpected CAPTCHA,
    2FA that can't be automated, session expiry.
    """
    pass


class CheckpointRecoveryError(JobFinderError):
    """Could not restore workflow from checkpoint. Restart required.

    Raised when a checkpointed state is corrupt or incompatible
    with the current workflow definition.
    """
    pass


class VaultError(JobFinderError):
    """Error accessing the PII vault or account vault.

    Covers encryption/decryption failures, missing keys, and
    database access errors.
    """
    pass
