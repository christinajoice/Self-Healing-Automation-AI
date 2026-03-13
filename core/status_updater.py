from core.execution_status import execution_status

def update_status(
    execution_id: str,
    state: str,
    message: str,
    progress: int = None,
    error: str = None
):
    execution_status[execution_id] = {
        "state": state,
        "message": message,
        "progress": progress,
        "error": error
    }
