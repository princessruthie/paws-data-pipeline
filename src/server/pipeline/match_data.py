import datetime, time
import pandas as pd
import numpy as np

from flask import current_app
from pipeline import log_db
from sqlalchemy.sql import text


def normalize_before_match(value):
    result = None

    if isinstance(value, str):
        result = value.lower()

    return result

def delete_from_db(connection, id_list):
    for id in id_list:
        sql = text("delete from pdp_contacts where matching_id = :id")
        connection.execute(sql, id=id)


def start(connection, added_or_updated_rows, manual_matches_df, job_id):
    # Match new records to each other and existing pdp_contacts data.
    # Assigns matching ID's to records, as well.
    # WARNING: not thread-safe and could lead to concurrency issues if two users /execute simultaneously
    current_app.logger.info('Start record matching')
    # Will need to consider updating the existing row contents (filter by active), deactivate,
    # try to match, and merge previous matching groups if applicable
    # job_id = str(int(time.time()))
    log_db.log_exec_status(job_id, 'matching', 'executing', '')

    current_app.logger.info("***** Running execute job ID " + job_id + " *****")
    items_to_update = pd.concat([added_or_updated_rows["new"], added_or_updated_rows["updated"]], ignore_index=True)
    pdp_contacts = pd.read_sql_table('pdp_contacts', connection)

    if pdp_contacts["matching_id"].dropna().size == 0:
        max_matching_group = 0
    else:
        max_matching_group = max(pdp_contacts["matching_id"].dropna())

    # Initialize column metadata we'll write to pdp_contacts
    items_to_update["matching_id"] = 0  # initializing an int and overwrite in the loop
    items_to_update["archived_date"] = np.nan
    items_to_update["created_date"] = datetime.datetime.now()

    # Create Normalized columns for matching
    items_to_update["first_name_normalized"] = items_to_update["first_name"].apply(normalize_before_match)
    items_to_update["last_name_normalized"] = items_to_update["last_name"].apply(normalize_before_match)
    items_to_update["email_normalized"] = items_to_update["email"].apply(normalize_before_match)

    pdp_contacts["first_name_normalized"] = pdp_contacts["first_name"].apply(normalize_before_match)
    pdp_contacts["last_name_normalized"] = pdp_contacts["last_name"].apply(normalize_before_match)
    pdp_contacts["email_normalized"] = pdp_contacts["email"].apply(normalize_before_match)

    rows = items_to_update.to_dict(orient="records")
    row_print_freq = 1000
    id_changes = {}

    for row_num, row in enumerate(rows):
        if row_num % row_print_freq == 0:
            current_app.logger.info("- Matching rows {}-{} of {}".format(
                row_num + 1, min(len(rows), row_num + row_print_freq), len(rows))
            )
            log_db.log_exec_status(job_id, 'matching', 'executing', str({'at_row': row_num + 1, 'of_rows': len(rows)  }) )

        # Exact matches based on specified columns
        matching_condition = (
                (((pdp_contacts["first_name_normalized"] == row["first_name_normalized"]) &
                (pdp_contacts["last_name_normalized"] == row["last_name_normalized"]))
                |
                ((pdp_contacts["first_name_normalized"] == row["last_name_normalized"]) &
                (pdp_contacts["last_name_normalized"] == row["first_name_normalized"])))
                &
                ((pdp_contacts["email_normalized"] == row["email_normalized"]) | (pdp_contacts["mobile"] == row["mobile"]))
            )
        row_matches = pdp_contacts[matching_condition]
        #collect other linked ids from manual matches source
        if not manual_matches_df.empty:
            linked_ids = manual_matches_df[(manual_matches_df[row["source_type"]] == row["source_id"])]
            ids = linked_ids.to_dict(orient="records")
            for id_num, row_dict in enumerate(ids):
                for column, value in row_dict.items():
                    row_matches = row_matches.append(pdp_contacts[(pdp_contacts["source_type"] == column) & (pdp_contacts["source_id"] == value)])
                    
        
        if row_matches.empty:  # new record, no matching rows
            max_matching_group += 1
            row_group = max_matching_group
        else:  # existing match(es)
            row_group = row_matches["matching_id"].values[0]
            if not all(row_matches["matching_id"] == row_group):
                #add old ids that will be removed in the final step
                #will probably always be one?
                old_ids = row_matches[row_matches["matching_id"] != row_group]["matching_id"].tolist()
                
                #remap these ids after we iterate
                for old_id in old_ids:
                    id_changes[old_id] = row_group
                pdp_contacts.drop(pdp_contacts[matching_condition].index, inplace=True)
                row_matches = row_matches.assign(matching_id=row_group)
                pdp_contacts = pdp_contacts.append(row_matches, ignore_index=True)
                row_matches.drop(columns=["_id",  "first_name_normalized", "last_name_normalized", "email_normalized"], inplace=True)
                
                #write back old data not contained in the items_to_update dataframe
                row_matches = row_matches[~row_matches['source_id'].isin(items_to_update['source_id'].tolist())]
                row_matches.to_sql('pdp_contacts', connection, index=False, if_exists='append')
                current_app.logger.warning(
                    "Source {} with ID {} is matching multiple groups in pdp_contacts ({})"
                        .format(row["source_type"], row["source_id"], str(row_matches["matching_id"].drop_duplicates()))
                )
        items_to_update.loc[row_num, "matching_id"] = row_group
        # Updating local pdp_contacts dataframe instead of a roundtrip to postgres within the loop.
        # Indexing by iloc and vector of rows to keep the pd.DataFrame class and avoid implicit
        # casting to a single-typed pd.Series.
        pdp_contacts = pdp_contacts.append(items_to_update.iloc[[row_num], :], ignore_index=True)

    # Make Id changes
    for key in id_changes:
        items_to_update.loc[items_to_update["matching_id"] == key, ["matching_id"]] = id_changes[key]
    # Write new data and matching ID's to postgres in bulk, instead of line-by-line
    delete_from_db(connection, id_changes)
    current_app.logger.info("- Writing data to pdp_contacts table")
    items_to_update = items_to_update.drop(
        columns=["first_name_normalized", "last_name_normalized", "email_normalized"])
    items_to_update.to_sql('pdp_contacts', connection, index=False, if_exists='append')
    current_app.logger.info("- Finished load to pdp_contacts table")

    log_db.log_exec_status(job_id, 'matching', 'executing', str({'at_row': len(rows), 'of_rows': len(rows) }) )
