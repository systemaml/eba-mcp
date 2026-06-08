import re


SLUGGED_EBA_ID_PATTERN = re.compile(
    r"^(EBA|JC)-(LARGE-[A-Za-z]+|[A-Za-z]+)-(\d{4})-(\d{1,4})$"
)


def canonicalize_eba_id(value: str) -> str:
    """Return slash-separated official EBA/JC IDs from slug-like directory IDs.

    Examples:
    - EBA-GL-2021-02 -> EBA/GL/2021/02
    - EBA-Op-2022-01 -> EBA/Op/2022/01
    - EBA-LARGE-GL-0000-0070 -> EBA/LARGE-GL/0000/0070
    """

    match = SLUGGED_EBA_ID_PATTERN.match(value.strip())
    if not match:
        return value

    authority, document_type, year, number = match.groups()
    return f"{authority}/{document_type}/{year}/{number}"


def slugify_eba_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", value.replace("/", "-")).strip("-")
