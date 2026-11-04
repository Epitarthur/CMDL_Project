"Persist version-linked assessments once, outside recommendation request paths."

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cmdl.quality.checks import VERSION, assess, duplicate_keys


def check_all(connection):
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT * FROM metadata_versions ORDER BY metadata_version_id"
        ).fetchall()
    done = {
        str(row[0])
        for row in connection.execute(
            "SELECT metadata_version_id FROM metadata_assessments WHERE checks_version=%s",
            (VERSION,),
        )
    }
    duplicates = duplicate_keys(records)
    count = 0
    for record in records:
        if str(record["metadata_version_id"]) not in done:
            persist(connection, record, duplicates)
            count += 1
    return count


def persist(connection, record, duplicates=None):
    result = assess(record, duplicates)
    connection.execute(
        "INSERT INTO metadata_assessments VALUES (%s,%s,%s,%s,DEFAULT) ON CONFLICT DO NOTHING",
        (record["metadata_version_id"], VERSION, result["quality_run_id"], Jsonb(result)),
    )
    for warning in result["warnings"]:
        connection.execute(
            "INSERT INTO assessment_warnings VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                warning["warning_id"],
                result["quality_run_id"],
                record["metadata_version_id"],
                Jsonb(warning),
            ),
        )
    return result
