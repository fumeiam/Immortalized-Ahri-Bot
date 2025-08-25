import logging, sys, time, json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {"level": record.levelname, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)), "msg": record.getMessage(), "logger": record.name}
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)

def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)

configure_logging()
