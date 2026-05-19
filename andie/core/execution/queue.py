from queue import Queue

task_queue = Queue()

def enqueue(task):
    task_queue.put(task)

def dequeue():
    return task_queue.get()
