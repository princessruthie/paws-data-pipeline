import os
import copy

import pandas as pd
import numpy as np

from fuzzywuzzy import fuzz
from config import REPORT_PATH, engine
import datetime

from flask import current_app
from config import engine
from datasource_manager import DATASOURCE_MAPPING


# Matching columns and table order priority as established by
# https://github.com/CodeForPhilly/paws-data-pipeline/blob/master/documentation/matching_rules.md
MATCH_FIELDS = ['email', 'name']
MATCH_PRIORITY = ['salesforcecontacts', 'volgistics', 'petpoint']


def single_fuzzy_score(record1, record2, case_sensitive=False):
    # Calculate a fuzzy matching score between two strings.
    # Uses a modified Levenshtein distance from the fuzzywuzzy package.
    # Update this function if a new fuzzy matching algorithm is selected.
    # Similar to the example of "New York Yankees" vs. "Yankees" in the documentation, we 
    # should use fuzz.partial_ratio instead of fuzz.ratio to more gracefully handle nicknames.
    # https://chairnerd.seatgeek.com/fuzzywuzzy-fuzzy-string-matching-in-python/
    if not case_sensitive:
        record1 = record1.lower()
        record2 = record2.lower()
    return fuzz.partial_ratio(record1, record2)


def df_fuzzy_score(df, column1_name, column2_name, **kwargs):
    # Calculates a new column of fuzzy scores from two columns of strings.
    # Slow in part due to a nonvectorized loop over rows
    if df.empty:
        return []
    else:
        return df.apply(lambda row: single_fuzzy_score(row[column1_name], row[column2_name], **kwargs), axis=1)


def join_on_all_columns(master_df, table_to_join):
    # attempts to join based on all columns
    left_right_indicator='_merge'
    join_results = master_df.merge(table_to_join, how='outer', indicator=left_right_indicator)
    return (
        join_results[join_results[left_right_indicator]=='both'].drop(columns=left_right_indicator),
        join_results[join_results[left_right_indicator]=='left_only'].drop(columns=left_right_indicator),
        join_results[join_results[left_right_indicator]=='right_only'].drop(columns=left_right_indicator)
    )


def coalesce_fields_by_id(master_table, other_tables, fields):
    # Add fields to master_table from other_tables, in priority order of how they're listed.
    # Similar in intent to running a SQL COALESCE based on the join to each field.
    df_with_updated_fields = master_table.copy()
    df_with_updated_fields[fields] = np.nan
    for table in other_tables:
        # may need to adjust the loop based on the master ID, renaming the fields, depending on how the PK is stored in the volgistics table, etc.
        fields_from_table = master_table.merge(table, how='left', validate='1:1')
        for field_to_update in fields:
            df_with_updated_fields[field_to_update] = df_with_updated_fields[field_to_update].combine_first(fields_from_table[field_to_update])
    return df_with_updated_fields


def normalize_table_for_comparison(df, cols):
    # Standardize specified columns to avoid common/irrelevant sources of mismatching (lowercase, etc)
    out_df = df.copy()
    for column in cols:
        out_df[column] = out_df[column].str.strip().str.lower()
    return out_df


def combine_fields(df, fields, sep=' '):
    if isinstance(fields, str):
        return df[fields]
    else:
        assert isinstance(fields, list)
    if len(fields) == 1:
        return df[fields[0]]
    else:
        return df[fields[0]].str.cat(others=df[fields[1:]], sep=sep)


def _reassign_combined_fields(df, field_mapping):
    out_df = df.copy()
    for new_field, old_field in field_mapping.items():
        out_df[new_field] = combine_fields[out_df, old_field]
    return out_df


def _get_master_df():
    # FIXME: stub which should be replaced by load_paws_data, create_master_df, or another database-aware file
    # Gets the full master dataframe from postgres so we can identify new vs. old matches
    # NOTE: master_df will be initialized upstream of me, along with the other tables
    # TODO: replace this line with a query (then add back in the coalesce code for regenerating the name+email of record...possibly in the 360 view?)
    return pd.DataFrame(columns=['email', 'name', 'salesforcecontacts_id', 'volgistics_id', 'petpoint_id'])  # currently an empty placeholder


def _get_most_recent_table_records_from_postgres(table_name):
    select_query = f'select * from {table_name} where archived_date is null'
    return pd.read_sql(select_query, engine)


def _get_table_primary_key(source_name):
    return DATASOURCE_MAPPING[source_name]['id']


def _get_master_primary_key(source_name):
    return source_name + '_id'


def start(added_or_updated_rows):
    # Match newly identified records to each other and existing master data

    # TODO: handling empty json within added_or_updated rows
    # TODO: Log any changes to name or email to a file + visual notification for human review and handling?

    if len(added_or_updated_rows['updated_rows']) > 0:  # any updated rows
        raise NotImplementedError("match_data.start cannot yet handle row updates.")

    # Recreate the normalized MATCH_FIELDS of-record based on the available source data based on the MATCH_PRIORITY order
    master_df = normalize_table_for_comparison(_get_master_df(), MATCH_FIELDS)  # FIXME: does this table even exist in the load_paws_data.py or before the matching script is called?
    master_fields = master_df.columns

    # Combine all of the loaded new_rows into a single dataframe
    new_df = pd.DataFrame({col: [] for col in MATCH_FIELDS})  # init an empty dataframe to collect the new data in one place
    for table_name in MATCH_PRIORITY:
        if table_name not in added_or_updated_rows['new_rows'].keys():
            continue  # df is empty or not-updated, so there's nothing to do
            # FIXME: handling empty df here?
        table_csv_key = _get_table_primary_key(table_name)
        table_master_key = _get_master_primary_key(table_name)
        table_cols = copy.deepcopy(MATCH_FIELDS)
        table_cols.append(table_csv_key)

        new_table_data = (
            pd.DataFrame(added_or_updated_rows['new_rows'][table_name])
            .pipe(_reassign_combined_fields, DATASOURCE_MAPPING[table_name]['identifying_criteria'])
            [table_cols]
            .pipe(lambda df: normalize_table_for_comparison(df, MATCH_FIELDS))
            .rename(columns={table_csv_key: table_master_key})
        )
        new_df = new_df.merge(new_table_data, how='outer')

    # Run the join, then report only the original fields of interest
    matches, left_only, right_only = join_on_all_columns(master_df[MATCH_FIELDS], new_df[MATCH_FIELDS])
    #new_master_rows = new_df[new_df['_temp_new_id'] in right_only['_temp_new_id']][master_fields]
    new_master_rows = right_only.merge(new_df, how='inner')  # associate name+email back to the IDs
    # need to strip name+email off

    # TODO LATER, after getting the new fields working, first.  Also should report the old version of the row.
    #updated_master_rows = coalesce_fields_by_id(
    #    master_df[master_fields],  # TODO: still need to figure out this field
    #    new_df['_temp_new_id' in matches['_temp_new_id']][master_fields]
    #)

    if True:
        current_app.logger.info('****BEGIN MATCHING DEBUG****')
        current_app.logger.info('input data: {}'.format(str(added_or_updated_rows)))
        
        current_app.logger.info('new_df: {}'.format(str(new_df.to_dict(orient='records'))))

        current_app.logger.info('matches: {}'.format(str(matches.to_dict(orient='records'))))
        current_app.logger.info('left_only: {}'.format(str(left_only.to_dict(orient='records'))))
        current_app.logger.info('right_only: {}'.format(str(right_only.to_dict(orient='records'))))
        
        current_app.logger.info('Returning new master rows: {}'.format(str(new_master_rows.to_dict(orient='records'))))
        current_app.logger.info('****END MATCHING DEBUG****')
    return {'new_matches': new_master_rows.to_dict(orient='records'), 'updated_matches': ['empty_for_now']}
