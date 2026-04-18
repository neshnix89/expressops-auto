"""
Mock Data Capture Script
========================
Run this on the company laptop to save live API responses as mock data.
This data is then committed to git so Claude Code on VPS can test without live access.

Usage:
    ops capture <task_name>
    python scripts/capture_mock_data.py --task <task_name>

Each task's TASK.md should list what mock data is needed under "Mock Data Needed".
This script provides a generic framework — task-specific capture logic should be
added as functions below.
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.jira_client import JiraClient
from core.m3 import M3Client
from core.confluence import ConfluenceClient
from core.logger import get_logger


def capture_generic_jira(jira: JiraClient, task_dir: Path, logger):
    """
    Generic JIRA capture — saves search results for common queries.
    Override in task-specific capture functions for custom queries.
    """
    mock_dir = task_dir / "mock_data"
    mock_dir.mkdir(parents=True, exist_ok=True)

    # Example: Capture all Work Containers across projects (scoped by Order Type,
    # customfield_13905, since NPI containers span many JIRA project keys).
    logger.info("Capturing JIRA Work Containers...")
    try:
        jql = 'issuetype = "Work Container" AND "Order Type" is not EMPTY ORDER BY key DESC'
        result = jira.search(jql, max_results=50)
        jira.save_mock(result, "search_work_containers.json", mock_dir)
        logger.info(f"  Saved {len(result.get('issues', []))} containers")
    except Exception as e:
        logger.error(f"  Failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Capture mock data from live systems")
    parser.add_argument("--task", required=True, help="Task name (folder name under tasks/)")
    args = parser.parse_args()

    task_dir = PROJECT_ROOT / "tasks" / args.task
    if not task_dir.exists():
        print(f"[ERROR] Task directory not found: {task_dir}")
        sys.exit(1)

    logger = get_logger(f"capture_{args.task}")
    config = load_config(mode_override="live")  # Must be live to capture real data

    mock_dir = task_dir / "mock_data"
    mock_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Capturing mock data for task: {args.task}")
    logger.info(f"Output directory: {mock_dir}")

    # Check if the task has a custom capture module
    capture_module_path = task_dir / "capture.py"
    if capture_module_path.exists():
        logger.info("Found task-specific capture.py — running custom capture...")
        spec = importlib.util.spec_from_file_location(f"tasks.{args.task}.capture", capture_module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "capture"):
            mod.capture(config, mock_dir, logger)
        else:
            logger.warning("capture.py exists but has no capture() function. Running generic capture.")
            _run_generic(config, task_dir, logger)
    else:
        logger.info("No custom capture.py — running generic JIRA capture...")
        _run_generic(config, task_dir, logger)

    logger.info("Mock data capture complete.")
    logger.info(f"Files saved to: {mock_dir}")
    logger.info("Commit these files to git so VPS can use them for testing.")


def _run_generic(config, task_dir, logger):
    jira = JiraClient(config)
    capture_generic_jira(jira, task_dir, logger)


if __name__ == "__main__":
    main()
