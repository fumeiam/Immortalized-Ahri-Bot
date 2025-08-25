import importlib, pkgutil, logging

async def load_features(bot):
    import features
    prefix = features.__name__ + "."
    for m in pkgutil.iter_modules(features.__path__):
        name = prefix + m.name
        try:
            mod = importlib.import_module(name)
            if hasattr(mod, "setup"):
                res = mod.setup(bot)
                if hasattr(res, "__await__"):
                    await res
            info = getattr(mod, "FEATURE_INFO", None)
            if info:
                bot.feature_info[info.get("name", m.name)] = {"triggers": info.get("triggers", [])}
        except Exception as e:
            logging.exception("Failed to load feature %s: %s", name, e)
            bot.failed_modules.append(m.name)
