from datetime import datetime, timedelta
from enum import Enum
import uuid

from flask import Blueprint, jsonify, request

from smart_invoice_pro.utils.cosmos_client import recurring_profiles_container
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity
from smart_invoice_pro.utils.bulk_archive_contracts import (
    add_archive_failure,
    add_archive_success,
    finalize_bulk_archive_result,
    init_bulk_archive_result,
)
from smart_invoice_pro.utils.domain_events import record_bulk_archive_completed
from smart_invoice_pro.utils.audit_logger import log_bulk_archive_summary


recurring_profiles_blueprint = Blueprint('recurring_profiles', __name__)


class RecurringStatus(Enum):
    Active = 'Active'
    Paused = 'Paused'
    Completed = 'Completed'
    Cancelled = 'Cancelled'
    # Legacy statuses still accepted for backward compatibility.
    Expired = 'Expired'
    Stopped = 'Stopped'


class FrequencyType(Enum):
    Weekly = 'Weekly'
    Monthly = 'Monthly'
    Yearly = 'Yearly'
    Custom = 'Custom'
    # Legacy values still accepted for existing profiles.
    Daily = 'Daily'
    Quarterly = 'Quarterly'


_ALLOWED_SORT_FIELDS = {
    'created_at',
    'updated_at',
    'profile_name',
    'customer_name',
    'amount',
    'frequency',
    'next_run_date',
    'last_run_date',
    'status',
}

_VALID_ENDS_TYPES = {'never', 'on_date', 'after_occurrences'}


def _is_archived(item):
    return str(item.get('status') or '').strip().upper() == 'ARCHIVED'


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).date()
    except ValueError:
        return None


def _clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def _days_in_month(year, month):
    if month == 2:
        leap = (year % 400 == 0) or (year % 4 == 0 and year % 100 != 0)
        return 29 if leap else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _normalize_week_days(values):
    if not isinstance(values, list):
        return []
    normalized = []
    for value in values:
        as_int = _to_int(value)
        if as_int is not None and 0 <= as_int <= 6:
            normalized.append(as_int)
    return sorted(set(normalized))


def _normalize_frequency(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    mapping = {
        'daily': 'Daily',
        'weekly': 'Weekly',
        'monthly': 'Monthly',
        'quarterly': 'Quarterly',
        'yearly': 'Yearly',
        'custom': 'Custom',
    }
    return mapping.get(text.lower(), text)


def _normalize_status(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    mapping = {
        'active': 'Active',
        'paused': 'Paused',
        'completed': 'Completed',
        'cancelled': 'Cancelled',
        'expired': 'Expired',
        'stopped': 'Stopped',
    }
    return mapping.get(text.lower(), text)


def _calculate_amount(data):
    items = data.get('items') or []
    if not isinstance(items, list):
        return 0.0

    subtotal = 0.0
    for item in items:
        qty = max(0.0, _to_float(item.get('quantity', 0)))
        rate = max(0.0, _to_float(item.get('rate', 0)))
        discount = max(0.0, _to_float(item.get('discount', 0)))
        line = max(0.0, qty * rate - discount)
        tax = max(0.0, _to_float(item.get('tax', 0)))
        line += (line * tax) / 100.0
        subtotal += line

    cgst = max(0.0, _to_float(data.get('cgst_amount', 0)))
    sgst = max(0.0, _to_float(data.get('sgst_amount', 0)))
    igst = max(0.0, _to_float(data.get('igst_amount', 0)))
    return subtotal + cgst + sgst + igst


def _build_recurrence_rule(data):
    start_date = _parse_date(data.get('start_date'))
    base_rule = data.get('recurrence_rule') if isinstance(data.get('recurrence_rule'), dict) else {}

    frequency = _normalize_frequency(
        base_rule.get('frequency')
        or data.get('frequency')
        or 'Monthly'
    )
    interval = _to_int(
        base_rule.get('interval')
        if base_rule.get('interval') is not None
        else data.get('recurrence_interval'),
        1,
    )
    interval = max(1, interval or 1)

    default_day = start_date.day if start_date else 1
    default_month = start_date.month if start_date else 1

    weekly_days = _normalize_week_days(
        base_rule.get('weekly_days')
        if 'weekly_days' in base_rule
        else data.get('recurrence_week_days')
    )
    day_of_month = _to_int(
        base_rule.get('day_of_month')
        if base_rule.get('day_of_month') is not None
        else data.get('recurrence_day_of_month'),
        default_day,
    )
    month_of_year = _to_int(
        base_rule.get('month_of_year')
        if base_rule.get('month_of_year') is not None
        else data.get('recurrence_month_of_year'),
        default_month,
    )

    day_of_month = _clamp(day_of_month or default_day, 1, 31)
    month_of_year = _clamp(month_of_year or default_month, 1, 12)

    ends_type = str(
        data.get('ends_type')
        or base_rule.get('ends_type')
        or ('on_date' if data.get('end_date') else 'after_occurrences' if data.get('occurrence_limit') else 'never')
    ).strip().lower()
    if ends_type not in _VALID_ENDS_TYPES:
        ends_type = 'never'

    end_date = data.get('end_date') if ends_type == 'on_date' else None
    occurrence_limit = None
    if ends_type == 'after_occurrences':
        occurrence_limit = _to_int(
            data.get('occurrence_limit')
            if data.get('occurrence_limit') not in (None, '')
            else base_rule.get('occurrence_limit'),
            None,
        )

    return {
        'frequency': frequency,
        'interval': interval,
        'weekly_days': weekly_days,
        'day_of_month': day_of_month,
        'month_of_year': month_of_year,
        'ends_type': ends_type,
        'end_date': end_date,
        'occurrence_limit': occurrence_limit,
    }


def calculate_next_run_date(current_date, frequency, recurrence_rule=None):
    """Calculate next run date using frequency and optional recurrence rule."""
    dt = _parse_date(current_date)
    if not dt:
        return current_date

    normalized_frequency = _normalize_frequency(frequency)
    recurrence_rule = recurrence_rule or {}
    interval = max(1, _to_int(recurrence_rule.get('interval'), 1) or 1)

    if normalized_frequency == 'Daily':
        return (dt + timedelta(days=interval)).isoformat()
    if normalized_frequency == 'Weekly':
        weekly_days = _normalize_week_days(recurrence_rule.get('weekly_days'))
        if not weekly_days:
            return (dt + timedelta(weeks=interval)).isoformat()

        current_dow = int((dt.weekday() + 1) % 7)
        next_dow = next((day for day in weekly_days if day > current_dow), None)
        if next_dow is not None:
            return (dt + timedelta(days=(next_dow - current_dow))).isoformat()

        days_until_week_end = 7 - current_dow
        week_jump_days = max(0, interval - 1) * 7
        return (dt + timedelta(days=days_until_week_end + week_jump_days + weekly_days[0])).isoformat()

    if normalized_frequency == 'Monthly':
        next_month = dt.month + interval
        next_year = dt.year
        while next_month > 12:
            next_month -= 12
            next_year += 1

        target_day = _to_int(recurrence_rule.get('day_of_month'), dt.day) or dt.day
        target_day = _clamp(target_day, 1, _days_in_month(next_year, next_month))
        return dt.replace(year=next_year, month=next_month, day=target_day).isoformat()

    if normalized_frequency == 'Quarterly':
        return (dt + timedelta(days=90)).isoformat()

    if normalized_frequency == 'Yearly':
        next_year = dt.year + interval
        target_month = _clamp(_to_int(recurrence_rule.get('month_of_year'), dt.month) or dt.month, 1, 12)
        target_day_raw = _to_int(recurrence_rule.get('day_of_month'), dt.day) or dt.day
        target_day = _clamp(target_day_raw, 1, _days_in_month(next_year, target_month))
        return dt.replace(year=next_year, month=target_month, day=target_day).isoformat()

    return dt.isoformat()


def _generate_schedule_preview(start_date, recurrence_rule, count=5):
    dt = _parse_date(start_date)
    if not dt:
        return []

    dates = []
    end_date = _parse_date(recurrence_rule.get('end_date'))
    occurrence_limit = _to_int(recurrence_rule.get('occurrence_limit'), None)

    while len(dates) < count:
        if recurrence_rule.get('ends_type') == 'on_date' and end_date and dt > end_date:
            break
        if (
            recurrence_rule.get('ends_type') == 'after_occurrences'
            and occurrence_limit
            and len(dates) >= occurrence_limit
        ):
            break

        dates.append(dt.isoformat())
        next_date = calculate_next_run_date(dt.isoformat(), recurrence_rule.get('frequency'), recurrence_rule)
        parsed_next = _parse_date(next_date)
        if not parsed_next or parsed_next <= dt:
            break
        dt = parsed_next

    return dates


def validate_recurring_profile_data(data, is_update=False):
    errors = {}
    if not isinstance(data, dict):
        return {'payload': 'Invalid JSON payload'}

    required_fields = ['profile_name', 'customer_id', 'frequency', 'start_date']
    if not is_update:
        for field in required_fields:
            if not data.get(field):
                errors[field] = f'{field} is required'

    if 'profile_name' in data and not str(data.get('profile_name', '')).strip():
        errors['profile_name'] = 'profile_name is required'

    recurrence_rule = _build_recurrence_rule(data)

    if 'frequency' in data or data.get('recurrence_rule'):
        frequency = _normalize_frequency(recurrence_rule.get('frequency'))
        if frequency not in FrequencyType._value2member_map_:
            errors['frequency'] = f'Invalid frequency: {data.get("frequency")}'

    if 'status' in data:
        status = _normalize_status(data.get('status'))
        if status not in RecurringStatus._value2member_map_:
            errors['status'] = f'Invalid status: {data.get("status")}'

    start_date = _parse_date(data.get('start_date')) if data.get('start_date') else None
    end_date = _parse_date(data.get('end_date')) if data.get('end_date') else None
    if data.get('start_date') and not start_date:
        errors['start_date'] = 'Invalid date format (expected YYYY-MM-DD)'
    if data.get('end_date') and not end_date:
        errors['end_date'] = 'Invalid date format (expected YYYY-MM-DD)'
    if start_date and end_date and end_date <= start_date:
        errors['end_date'] = 'End date must be after start date'

    interval = _to_int(recurrence_rule.get('interval'), 1)
    if interval is None or interval < 1:
        errors['recurrence_interval'] = 'recurrence_interval must be >= 1'

    if recurrence_rule.get('frequency') == 'Weekly' and not recurrence_rule.get('weekly_days'):
        errors['recurrence_week_days'] = 'Select at least one weekday for weekly recurrence'

    if recurrence_rule.get('ends_type') not in _VALID_ENDS_TYPES:
        errors['ends_type'] = 'Invalid ends_type. Allowed: never, on_date, after_occurrences'

    if recurrence_rule.get('ends_type') == 'on_date':
        if not data.get('end_date'):
            errors['end_date'] = 'end_date is required when ends_type is on_date'
        elif start_date and end_date and end_date <= start_date:
            errors['end_date'] = 'End date must be after start date'

    if recurrence_rule.get('ends_type') == 'after_occurrences':
        if recurrence_rule.get('occurrence_limit') is None:
            errors['occurrence_limit'] = 'occurrence_limit is required when ends_type is after_occurrences'
        elif recurrence_rule.get('occurrence_limit') < 1:
            errors['occurrence_limit'] = 'occurrence_limit must be a positive integer'

    occurrence_limit = data.get('occurrence_limit')
    if occurrence_limit not in (None, '',):
        try:
            if int(occurrence_limit) < 1:
                errors['occurrence_limit'] = 'occurrence_limit must be >= 1'
        except (TypeError, ValueError):
            errors['occurrence_limit'] = 'occurrence_limit must be a positive integer'

    return errors


def _query_single_profile(profile_id, tenant_id):
    query = 'SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id'
    params = [
        {'name': '@id', 'value': profile_id},
        {'name': '@tenant_id', 'value': tenant_id},
    ]
    items = list(recurring_profiles_container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
    ))
    if not items:
        return None
    # Return a copy so mutations do not leak to caller-held/shared fixtures.
    return dict(items[0])


def _sanitize_profile(profile):
    return {k: v for k, v in profile.items() if not k.startswith('_')}


def _set_profile_status(profile_id, next_status):
    tenant_id = request.tenant_id
    profile = _query_single_profile(profile_id, tenant_id)
    if not profile:
        return jsonify({'error': 'Recurring profile not found'}), 404
    if _is_archived(profile):
        return jsonify({'error': 'Archived recurring profiles cannot change status'}), 409

    profile['status'] = next_status
    profile['updated_at'] = datetime.utcnow().isoformat()
    updated = recurring_profiles_container.replace_item(item=profile['id'], body=profile)
    return jsonify(_sanitize_profile(updated)), 200


@recurring_profiles_blueprint.route('/recurring-profiles', methods=['POST'])
@recurring_profiles_blueprint.route('/recurring-invoices', methods=['POST'])
def create_recurring_profile():
    data = request.get_json() or {}
    errors = validate_recurring_profile_data(data)
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400

    now = datetime.utcnow().isoformat()
    tenant_id = request.tenant_id

    start_date = data.get('start_date')
    recurrence_rule = _build_recurrence_rule(data)
    frequency = recurrence_rule.get('frequency')
    preview = _generate_schedule_preview(start_date, recurrence_rule, 1)
    next_run_date = data.get('next_run_date') or (preview[0] if preview else start_date)

    item = {
        'id': str(uuid.uuid4()),
        'tenant_id': tenant_id,
        'profile_name': str(data.get('profile_name', '')).strip(),
        'customer_id': data.get('customer_id'),
        'customer_name': data.get('customer_name', ''),
        'amount': _to_float(data.get('amount', _calculate_amount(data))),
        'frequency': frequency,
        'recurrence_rule': recurrence_rule,
        'recurrence_interval': recurrence_rule.get('interval'),
        'recurrence_week_days': recurrence_rule.get('weekly_days'),
        'recurrence_day_of_month': recurrence_rule.get('day_of_month'),
        'recurrence_month_of_year': recurrence_rule.get('month_of_year'),
        'ends_type': recurrence_rule.get('ends_type'),
        'start_date': start_date,
        'end_date': recurrence_rule.get('end_date'),
        'occurrence_limit': recurrence_rule.get('occurrence_limit'),
        'occurrences_created': int(data.get('occurrences_created', 0) or 0),
        'next_run_date': next_run_date,
        'last_run_date': data.get('last_run_date') or None,
        'status': _normalize_status(data.get('status')) or 'Active',
        'auto_send': bool(data.get('auto_send', data.get('email_reminder', False))),
        'email_reminder': bool(data.get('email_reminder', data.get('auto_send', False))),
        'items': data.get('items', []),
        'payment_terms': data.get('payment_terms', ''),
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'is_gst_applicable': bool(data.get('is_gst_applicable', False)),
        'cgst_amount': _to_float(data.get('cgst_amount', 0.0)),
        'sgst_amount': _to_float(data.get('sgst_amount', 0.0)),
        'igst_amount': _to_float(data.get('igst_amount', 0.0)),
        'created_at': now,
        'updated_at': now,
    }

    try:
        created_item = recurring_profiles_container.create_item(body=item)
        return jsonify(_sanitize_profile(created_item)), 201
    except Exception as e:
        return jsonify({'error': f'Failed to create recurring profile: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles', methods=['GET'])
@recurring_profiles_blueprint.route('/recurring-invoices', methods=['GET'])
def get_recurring_profiles():
    try:
        tenant_id = request.tenant_id

        status_filter = _normalize_status(request.args.get('status'))
        frequency_filter = _normalize_frequency(request.args.get('frequency'))
        customer_id_filter = request.args.get('customer_id')
        search_query = (request.args.get('q') or '').strip().lower()

        date_range = (request.args.get('date_range') or '').strip().lower()
        date_from = (request.args.get('date_from') or '').strip()
        date_to = (request.args.get('date_to') or '').strip()

        sort_by = request.args.get('sort_by', 'created_at')
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'created_at'
        sort_order = (request.args.get('sort_order', 'desc') or 'desc').upper()
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        try:
            limit = int(request.args.get('limit', request.args.get('page_size', 10)))
        except ValueError:
            limit = 10
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit

        where = ['c.tenant_id = @tenant_id']
        parameters = [{'name': '@tenant_id', 'value': tenant_id}]

        if status_filter:
            where.append('c.status = @status')
            parameters.append({'name': '@status', 'value': status_filter})

        if frequency_filter:
            where.append('c.frequency = @frequency')
            parameters.append({'name': '@frequency', 'value': frequency_filter})

        if customer_id_filter:
            where.append('c.customer_id = @customer_id')
            parameters.append({'name': '@customer_id', 'value': customer_id_filter})

        if search_query:
            where.append('(CONTAINS(LOWER(c.profile_name), @q) OR CONTAINS(LOWER(c.customer_name), @q))')
            parameters.append({'name': '@q', 'value': search_query})

        if date_range:
            today = datetime.utcnow().date()
            start = None
            end = None
            if date_range == 'this_week':
                start = today - timedelta(days=today.weekday())
                end = start + timedelta(days=6)
            elif date_range == 'this_month':
                start = today.replace(day=1)
                if start.month == 12:
                    next_month = start.replace(year=start.year + 1, month=1, day=1)
                else:
                    next_month = start.replace(month=start.month + 1, day=1)
                end = next_month - timedelta(days=1)
            elif date_range == 'this_quarter':
                quarter_start_month = ((today.month - 1) // 3) * 3 + 1
                start = today.replace(month=quarter_start_month, day=1)
                if quarter_start_month == 10:
                    next_quarter = start.replace(year=start.year + 1, month=1, day=1)
                else:
                    next_quarter = start.replace(month=quarter_start_month + 3, day=1)
                end = next_quarter - timedelta(days=1)
            elif date_range == 'this_year':
                start = today.replace(month=1, day=1)
                end = today.replace(month=12, day=31)
            elif date_range == 'custom':
                if date_from:
                    parsed = _parse_date(date_from)
                    if parsed:
                        start = parsed
                if date_to:
                    parsed = _parse_date(date_to)
                    if parsed:
                        end = parsed

            if start:
                where.append('c.next_run_date >= @date_start')
                parameters.append({'name': '@date_start', 'value': start.isoformat()})
            if end:
                where.append('c.next_run_date <= @date_end')
                parameters.append({'name': '@date_end', 'value': end.isoformat()})

        where_sql = ' WHERE ' + ' AND '.join(where)

        count_query = f'SELECT VALUE COUNT(1) FROM c{where_sql}'
        count_items = list(recurring_profiles_container.query_items(
            query=count_query,
            parameters=parameters,
            enable_cross_partition_query=True,
        ))
        total = int(count_items[0]) if count_items else 0

        list_query = (
            f'SELECT * FROM c{where_sql} '
            f'ORDER BY c.{sort_by} {sort_order} '
            f'OFFSET @offset LIMIT @limit'
        )
        list_params = [*parameters, {'name': '@offset', 'value': offset}, {'name': '@limit', 'value': limit}]
        items = list(recurring_profiles_container.query_items(
            query=list_query,
            parameters=list_params,
            enable_cross_partition_query=True,
        ))

        cleaned = []
        for item in items:
            clean = _sanitize_profile(item)
            if clean.get('amount') in (None, ''):
                clean['amount'] = _calculate_amount(clean)
            cleaned.append(clean)

        return jsonify({
            'data': cleaned,
            'total': total,
            'page': page,
            'limit': limit,
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch recurring profiles: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['GET'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>', methods=['GET'])
def get_recurring_profile(profile_id):
    try:
        profile = _query_single_profile(profile_id, request.tenant_id)
        if not profile:
            return jsonify({'error': 'Recurring profile not found'}), 404
        return jsonify(_sanitize_profile(profile)), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch recurring profile: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['PUT'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>', methods=['PUT'])
def update_recurring_profile(profile_id):
    data = request.get_json() or {}
    errors = validate_recurring_profile_data(data, is_update=True)
    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400

    try:
        profile = _query_single_profile(profile_id, request.tenant_id)
        if not profile:
            return jsonify({'error': 'Recurring profile not found'}), 404
        if _is_archived(profile):
            return jsonify({'error': 'Recurring profile not found'}), 404

        for key, value in data.items():
            if key in {'id', 'tenant_id', 'created_at'}:
                continue
            if key == 'frequency':
                profile[key] = _normalize_frequency(value)
            elif key == 'status':
                profile[key] = _normalize_status(value)
            else:
                profile[key] = value

        recurrence_rule = _build_recurrence_rule(profile)
        profile['frequency'] = recurrence_rule.get('frequency')
        profile['recurrence_rule'] = recurrence_rule
        profile['recurrence_interval'] = recurrence_rule.get('interval')
        profile['recurrence_week_days'] = recurrence_rule.get('weekly_days')
        profile['recurrence_day_of_month'] = recurrence_rule.get('day_of_month')
        profile['recurrence_month_of_year'] = recurrence_rule.get('month_of_year')
        profile['ends_type'] = recurrence_rule.get('ends_type')
        profile['end_date'] = recurrence_rule.get('end_date')
        profile['occurrence_limit'] = recurrence_rule.get('occurrence_limit')

        start_for_preview = profile.get('start_date')
        preview = _generate_schedule_preview(start_for_preview, recurrence_rule, 1)
        if preview:
            profile['next_run_date'] = preview[0]

        profile['auto_send'] = bool(profile.get('auto_send', profile.get('email_reminder', False)))
        profile['email_reminder'] = bool(profile.get('email_reminder', profile.get('auto_send', False)))
        profile['amount'] = _to_float(profile.get('amount', _calculate_amount(profile)))
        profile['updated_at'] = datetime.utcnow().isoformat()

        updated_item = recurring_profiles_container.replace_item(item=profile['id'], body=profile)
        return jsonify(_sanitize_profile(updated_item)), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update recurring profile: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['PATCH'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>', methods=['PATCH'])
def patch_recurring_profile(profile_id):
    data = request.get_json() or {}
    action = str(data.get('action', '')).strip().lower()

    if action in {'pause', 'resume', 'cancel'}:
        if action == 'pause':
            return _set_profile_status(profile_id, 'Paused')
        if action == 'resume':
            return _set_profile_status(profile_id, 'Active')
        return _set_profile_status(profile_id, 'Cancelled')

    return update_recurring_profile(profile_id)


@recurring_profiles_blueprint.route('/recurring-profiles/bulk', methods=['POST'])
@recurring_profiles_blueprint.route('/recurring-profiles/bulk-archive', methods=['POST'])
@recurring_profiles_blueprint.route('/recurring-invoices/bulk', methods=['POST'])
def bulk_recurring_profile_actions():
    payload = request.get_json() or {}
    action = str(payload.get('action', '')).strip().lower()
    ids = payload.get('ids') or []

    if action not in {'pause', 'resume', 'delete', 'cancel'}:
        return jsonify({'error': 'Invalid bulk action'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty array'}), 400

    updated = 0
    deleted = 0
    errors = []
    archive_result = init_bulk_archive_result('recurring_profile', ids)

    for profile_id in ids:
        try:
            profile = _query_single_profile(profile_id, request.tenant_id)
            if not profile:
                errors.append({'id': profile_id, 'error': 'not found'})
                continue

            if action == 'delete':
                if _is_archived(profile):
                    add_archive_failure(archive_result, profile_id, 'ALREADY_ARCHIVED', 'Recurring profile already archived')
                    errors.append({'id': profile_id, 'error': 'already_archived'})
                    continue

                archive_entity(
                    recurring_profiles_container,
                    profile,
                    'recurring_profile',
                    request.tenant_id,
                    getattr(request, 'user_id', None),
                    reason='bulk_archive',
                )
                deleted += 1
                add_archive_success(archive_result, profile_id, metadata={'message': 'Recurring profile archived successfully'})
                continue

            if action == 'pause':
                profile['status'] = 'Paused'
            elif action == 'resume':
                profile['status'] = 'Active'
            elif action == 'cancel':
                profile['status'] = 'Cancelled'

            profile['updated_at'] = datetime.utcnow().isoformat()
            recurring_profiles_container.replace_item(item=profile['id'], body=profile)
            updated += 1
        except Exception as exc:
            errors.append({'id': profile_id, 'error': str(exc)})
            if action == 'delete':
                add_archive_failure(archive_result, profile_id, 'INTERNAL_ERROR', str(exc))

    if action == 'delete':
        finalize_bulk_archive_result(archive_result)
        log_bulk_archive_summary(
            tenant_id=request.tenant_id,
            user_id=getattr(request, 'user_id', None),
            entity_type='recurring_profile',
            requested_count=archive_result['requestedCount'],
            success_count=archive_result['successCount'],
            failed_count=archive_result['failedCount'],
            dependency_summary=archive_result.get('dependencySummary', {}),
        )
        record_bulk_archive_completed(
            request.tenant_id,
            getattr(request, 'user_id', None),
            'recurring_profile',
            archive_result,
        )

    response = {
        'action': action,
        'processed': len(ids),
        'updated': updated,
        'deleted': deleted,
        'errors': errors,
    }
    if action == 'delete':
        response.update({
            'successCount': archive_result['successCount'],
            'failedCount': archive_result['failedCount'],
            'archived': archive_result['archived'],
            'failed': archive_result['failed'],
            'dependencySummary': archive_result['dependencySummary'],
            'classification': archive_result['classification'],
        })
    return jsonify(response), 200


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['DELETE'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>', methods=['DELETE'])
def delete_recurring_profile(profile_id):
    try:
        profile = _query_single_profile(profile_id, request.tenant_id)
        if not profile:
            return jsonify({'error': 'Recurring profile not found'}), 404

        if _is_archived(profile):
            return jsonify({'message': 'Recurring profile already archived'}), 200

        archive_entity(
            recurring_profiles_container,
            profile,
            'recurring_profile',
            request.tenant_id,
            getattr(request, 'user_id', None),
            reason='archive_on_delete',
        )
        return jsonify({'message': 'Recurring profile archived successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to archive recurring profile: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/restore', methods=['POST'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>/restore', methods=['POST'])
def restore_recurring_profile(profile_id):
    try:
        profile = _query_single_profile(profile_id, request.tenant_id)
        if not profile:
            return jsonify({'error': 'Recurring profile not found'}), 404

        if not _is_archived(profile):
            return jsonify({'error': 'Recurring profile is not archived'}), 422

        restored = restore_entity(
            recurring_profiles_container,
            profile,
            'recurring_profile',
            request.tenant_id,
            getattr(request, 'user_id', None),
            reason='restore_from_archive',
        )
        return jsonify({'message': 'Recurring profile restored successfully', 'status': restored.get('status')}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to restore recurring profile: {str(e)}'}), 500


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/pause', methods=['POST', 'PATCH'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>/pause', methods=['POST', 'PATCH'])
def pause_recurring_profile(profile_id):
    return _set_profile_status(profile_id, 'Paused')


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/resume', methods=['POST', 'PATCH'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>/resume', methods=['POST', 'PATCH'])
def resume_recurring_profile(profile_id):
    return _set_profile_status(profile_id, 'Active')


@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/cancel', methods=['PATCH'])
@recurring_profiles_blueprint.route('/recurring-invoices/<profile_id>/cancel', methods=['PATCH'])
def cancel_recurring_profile(profile_id):
    return _set_profile_status(profile_id, 'Cancelled')
