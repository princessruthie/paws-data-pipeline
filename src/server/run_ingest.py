import argparse
from collections import OrderedDict

import petl

from datasource_manager import DATASOURCE_MAPPING

def main(source_file, source, **kwargs):
    """
    Proof of concept to showcase use of petl
    """
    # TODO: Integrate mapping into `DATASOURCE_MAPPING`
    source = (
        petl.fromcsv(source_file, header=DATASOURCE_MAPPING[source]["tracked_columns"]) # pylint: disable=no-member
            .skip(1)
            .convert(("First Name", "Last Name"), "lower")
            .fieldmap(OrderedDict([
                ("name", lambda row: f"{row['First Name']} {row['Last Name']}"),
                ("email", "Email")
            ]))
    )
    print(source)
    mock_master_data = [
        ("_id", "master_id", "name", "email"),
        (1, 10, "michael jordan", "michael@gmail.com"),
        (2, 20, "bruce wein", "non-matching-email@gmail.com"),
        (3, 30, "foo bar", "fizz@buzz.com")
    ]
    master = petl.wrap(mock_master_data)
    print(master)

    new_records = (
        petl.antijoin(source, master, key=["name", "email"])
    )
    print(new_records)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-file", type=str, required=True,
        help="Path of the file to ingest via PAWS data pipeline."
    )
    parser.add_argument(
        "--source", type=str, required=True, 
        choices=DATASOURCE_MAPPING.keys(),
        help="The source of the provided `source-file`.  e.g. 'petpoint'"
    )
    args = parser.parse_args()
    main(**vars(args))