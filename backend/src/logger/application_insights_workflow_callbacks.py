# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import logging
import time

# from dataclasses import asdict
from typing import (
    Any,
    Dict,
    Optional,
)

from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter
from datashaper.workflow.workflow_callbacks import NoopWorkflowCallbacks
from opentelemetry._logs import (
    get_logger_provider,
    set_logger_provider,
)
from opentelemetry.sdk._logs import (
    LoggerProvider,
    LoggingHandler,
)
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor


class ApplicationInsightsWorkflowCallbacks(NoopWorkflowCallbacks):
    """A logger that writes to an AppInsights Workspace."""

    _logger: logging.Logger
    _logger_name: str
    _logger_level: int
    _logger_level_name: str
    _properties: Dict[str, Any]
    _workflow_name: str
    _index_name: str
    _num_workflow_steps: int
    _processed_workflow_steps: list[str] = []

    def __init__(
        self,
        connection_string: str,
        logger_name: str | None = None,
        logger_level: int = logging.INFO,
        index_name: str = "",
        num_workflow_steps: int = 0,
        properties: Dict[str, Any] = {},
    ):
        """
        Initialize the AppInsightsReporter.

        Args:
            connection_string (str): The connection string for the App Insights instance.
            logger_name (str | None, optional): The name of the logger. Defaults to None.
            logger_level (int, optional): The logging level. Defaults to logging.INFO.
            index_name (str, optional): The name of an index. Defaults to "".
            num_workflow_steps (int): A list of workflow names ordered by their execution. Defaults to [].
            properties (Dict[str, Any], optional): Additional properties to be included in the log. Defaults to {}.
        """
        self._logger: logging.Logger
        self._logger_name = logger_name
        self._logger_level = logger_level
        self._logger_level_name: str = logging.getLevelName(logger_level)
        self._properties = properties
        self._workflow_name = "N/A"
        self._index_name = index_name
        self._num_workflow_steps = num_workflow_steps
        self._processed_workflow_steps = []  # maintain a running list of workflow steps that get processed
        """Create a new logger with an AppInsights handler."""
        self.__init_logger(connection_string=connection_string)

    def __init_logger(self, connection_string, max_logger_init_retries: int = 10):
        max_retry = max_logger_init_retries
        while not (hasattr(self, "_logger")):
            if max_retry == 0:
                raise Exception(
                    "Failed to create logger. Could not disambiguate logger name."
                )

            # generate a unique logger name
            current_time = str(time.time())
            unique_hash = hashlib.sha256(current_time.encode()).hexdigest()
            self._logger_name = f"{self.__class__.__name__}-{unique_hash}"
            if self._logger_name not in logging.Logger.manager.loggerDict:
                # attach azure monitor log exporter to logger provider
                logger_provider = LoggerProvider()
                set_logger_provider(logger_provider)
                exporter = AzureMonitorLogExporter(connection_string=connection_string)
                get_logger_provider().add_log_record_processor(
                    BatchLogRecordProcessor(
                        exporter=exporter,
                        schedule_delay_millis=60000,
                    )
                )
                # instantiate new logger
                self._logger = logging.getLogger(self._logger_name)
                self._logger.propagate = False
                # remove any existing handlers
                self._logger.handlers.clear()
                # fetch handler from logger provider and attach to class
                self._logger.addHandler(LoggingHandler())
                # set logging level
                self._logger.setLevel(logging.DEBUG)

            # reduce sentinel counter value
            max_retry -= 1

    def _format_details(self, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """
        Format the details dictionary to comply with the Application Insights structured
        logging Property column standard.

        Args:
            details (Dict[str, Any] | None): Optional dictionary containing additional details to log.

        Returns:
            Dict[str, Any]: The formatted details dictionary with custom dimensions.
        """
        if not isinstance(details, dict) or (details is None):
            return {}
        return {"custom_dimensions": {**self._properties, **unwrap_dict(details)}}

    def on_workflow_start(self, name: str, instance: object) -> None:
        """Execute this callback when a workflow starts."""
        self._workflow_name = name
        self._processed_workflow_steps.append(name)
        message = f"Index: {self._index_name} -- " if self._index_name else ""
        workflow_progress = (
            f" ({len(self._processed_workflow_steps)}/{self._num_workflow_steps})"
            if self._num_workflow_steps
            else ""
        )  # will take the form "(1/4)"
        message += f"Workflow{workflow_progress}: {name} started."
        details = {
            "workflow_name": name,
            # "workflow_instance": str(instance),
        }
        if self._index_name:
            details["index_name"] = self._index_name
        self._logger.info(
            message, stack_info=False, extra=self._format_details(details=details)
        )

    def on_workflow_end(self, name: str, instance: object) -> None:
        """Execute this callback when a workflow ends."""
        message = f"Index: {self._index_name} -- " if self._index_name else ""
        workflow_progress = (
            f" ({len(self._processed_workflow_steps)}/{self._num_workflow_steps})"
            if self._num_workflow_steps
            else ""
        )  # will take the form "(1/4)"
        message += f"Workflow{workflow_progress}: {name} complete."
        details = {
            "workflow_name": name,
            # "workflow_instance": str(instance),
        }
        if self._index_name:
            details["index_name"] = self._index_name
        self._logger.info(
            message, stack_info=False, extra=self._format_details(details=details)
        )

    def on_error(
        self,
        message: str,
        cause: Optional[BaseException] = None,
        stack: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """A call back handler for when an error occurs."""
        details = {} if details is None else details
        details = {"cause": str(cause), "stack": stack, **details}
        self._logger.error(
            message,
            exc_info=True,
            stack_info=False,
            extra=self._format_details(details=details),
        )

    def on_warning(self, message: str, details: Optional[dict] = None) -> None:
        """A call back handler for when a warning occurs."""
        self._logger.warning(
            message, stack_info=False, extra=self._format_details(details=details)
        )

    def on_log(self, message: str, details: Optional[dict] = None) -> None:
        """A call back handler for when a log message occurs."""
        self._logger.info(
            message, stack_info=False, extra=self._format_details(details=details)
        )

    def on_measure(
        self, name: str, value: float, details: Optional[dict] = None
    ) -> None:
        """A call back handler for when a measurement occurs."""
        raise NotImplementedError("on_measure() not supported by this logger.")


def unwrap_dict(input_dict, parent_key="", sep="_"):
    """
    Recursively unwraps a nested dictionary by flattening it into a single-level dictionary.

    Args:
        input_dict (dict): The input dictionary to be unwrapped.
        parent_key (str, optional): The parent key to be prepended to the keys of the unwrapped dictionary. Defaults to ''.
        sep (str, optional): The separator to be used between the parent key and the child key. Defaults to '_'.

    Returns:
        dict: The unwrapped dictionary.
    """
    items = []
    for k, v in input_dict.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(unwrap_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
