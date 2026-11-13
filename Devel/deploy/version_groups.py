"""Discover related GitHub repositories by versioned names.

No GitHub credentials, filesystem writes, or publication side effects. Grouping is
advisory: each repository retains its own visibility and hosting controls.
"""
import re
from collections import defaultdict

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EXPLICIT = re.compile(r"(?:[-_.]?v[-_.]?)([1-9][0-9]{0,2})$", re.I)
_NUMERIC = re.compile(r"[-_.]([1-9][0-9]{0,2})$")
_SEPARATORS = re.compile(r"[-_.]+")


def project_identity(full_name):
    """Return (family id, version or None, normalized name) for a repo.

    Only terminal vN / v-N / -N suffixes are treated as versions. Version
    tags inside unrelated words and year-like suffixes aren't stripped.
    """
    if not isinstance(full_name, str) or not _REPO.fullmatch(full_name):
        raise ValueError("invalid repository name")
    owner, name = full_name.split("/", 1)
    match = _EXPLICIT.search(name)
    if match is None:
        match = _NUMERIC.search(name)
    if match and not _SEPARATORS.sub("", name[:match.start()]):
        match = None
    if match:
        stem = name[:match.start()]
        number = int(match.group(1))
    else:
        stem, number = name, None
    stem = _SEPARATORS.sub("-", stem).strip("-").lower()
    if not stem:
        raise ValueError("empty normalized family")
    return owner.lower() + "/" + stem, number, stem


def grouped_projects(projects):
    """Build a browsable family catalog, preserving every repository record.

    Does not modify the input and does not choose, deploy, or publish an active
    version. An unversioned repo is displayed as the original, *not verified v1*.
    """
    buckets = defaultdict(list)
    for project in projects:
        repo = project["full_name"]
        family, version, name = project_identity(repo)
        buckets[family].append((version, name, dict(project)))
    families = []
    for key, members in sorted(buckets.items()):
        members.sort(key=lambda item: (item[0] if item[0] is not None else 0,
                                       item[2]["full_name"].casefold()))
        multi = len(members) > 1
        label = members[0][1].replace("-", " ").title()
        items = []
        for version, _, record in members:
            record["family_key"] = key
            record["version_number"] = version
            record["version_label"] = ("Original / unversioned" if version is None
                                       else "v" + str(version))
            record["grouped"] = multi
            items.append(record)
        families.append({"key": key, "label": label, "grouped": multi,
                         "versions": items})
    return families
