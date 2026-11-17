from dataclasses import dataclass
from liverecord.timeline.revisions import RevisionStore
from liverecord.archive.catalog import Catalog


@dataclass(frozen=True)
class AcquisitionItem:
    revision_id: str
    declaration: object


@dataclass(frozen=True)
class CollectionPlan:
    source_id: str
    revision_id: str | None
    items: tuple


def plan_missing(db, source_id):
    revision_id, revision = RevisionStore(db).current(source_id)
    if revision is None:
        return CollectionPlan(source_id, None, ())
    catalog = Catalog(db)
    items = tuple(AcquisitionItem(revision_id, declaration)
                  for declaration in revision.declarations
                  if catalog.get_ready(declaration.identity) is None)
    return CollectionPlan(source_id, revision_id, items)


def plan_revision(db, source_id, revision_id):
    revision = RevisionStore(db).load(revision_id)
    catalog = Catalog(db)
    items = tuple(AcquisitionItem(revision_id, declaration)
                  for declaration in revision.declarations
                  if catalog.get_ready(declaration.identity) is None)
    return CollectionPlan(source_id, revision_id, items)
