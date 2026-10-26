"""connector_kit: the contract and test kit every connector builds on (security plan step 2,
PLAN-connectors.md). A connector collects; the kit builds intake envelopes (intake's stage-1
schema, exactly), adds provenance, and sends them to the intake service with the connector's own
key. Intake is the only sink. Standard library only. See ../README.md."""
from .client import IntakeClient, RequestTooLarge, RetryPolicy, Submission
from .connector import GATE_HOSTS, Connector, ConnectorConfig, Item, ItemReport, RunReport
from .envelope import Envelope, InvalidEnvelope, Key, TooLarge, build
from .outcomes import Outcome
from .secret import Secret, load_key_file

__all__ = ["Connector", "ConnectorConfig", "Envelope", "GATE_HOSTS", "IntakeClient", "InvalidEnvelope", "Item",
           "ItemReport", "Key", "Outcome", "RequestTooLarge", "RetryPolicy", "RunReport", "Secret", "Submission",
           "TooLarge", "build", "load_key_file"]
