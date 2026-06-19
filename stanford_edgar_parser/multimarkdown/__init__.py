def __getattr__(name: str):
    if name in __all__:
        from . import multimarkdown

        return getattr(multimarkdown, name)
    raise AttributeError(name)

__all__ = ["convert_all_tables_to_mmd", "df_to_multimarkdown"]
