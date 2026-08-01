from aiogram.fsm.state import State, StatesGroup


class OverallState(StatesGroup):
    """Класс со всеми необходимыми состояниями"""

    start = State()
