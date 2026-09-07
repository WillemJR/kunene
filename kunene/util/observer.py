
from abc import ABC, abstractmethod
from functools import wraps

# Decorator to notify observers
def notify_observers(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        result = func(self, *args, **kwargs)
        self._notify_observers(f"Action \'{self.name}\' Done")
        return result
    return wrapper

# Observer interface
class Observer(ABC):
    @abstractmethod
    def update(self, message):
        pass

# Subject interface
class Subject(ABC):
    def __init__(self):
        self._observers = []

    def attach(self, observer):
        self._observers.append(observer)

    def detach(self, observer):
        self._observers.remove(observer)

    def _notify_observers(self, message):
        for observer in self._observers:
            observer.update(message)

