from apps.hotels.repository import _best_room_allocation


def test_best_room_allocation_prefers_exact_multi_room_fit():
    rooms = [
        {"id": 1, "room_number": "101", "capacity_adults": 3, "price_per_night": "100"},
        {"id": 2, "room_number": "102", "capacity_adults": 4, "price_per_night": "120"},
        {"id": 3, "room_number": "103", "capacity_adults": 6, "price_per_night": "150"},
    ]

    selection, total_price = _best_room_allocation(rooms, 7, nights=2)

    assert [room["id"] for room in selection] == [1, 2]
    assert [room["nights"] for room in selection] == [2, 2]
    assert [str(room["total_price"]) for room in selection] == ["200", "240"]
    assert str(total_price) == "440"


def test_best_room_allocation_returns_empty_when_impossible():
    rooms = [
        {"id": 1, "room_number": "101", "capacity_adults": 2, "price_per_night": "100"},
        {"id": 2, "room_number": "102", "capacity_adults": 2, "price_per_night": "120"},
    ]

    selection, total_price = _best_room_allocation(rooms, 7)

    assert selection == []
    assert total_price is None


def test_best_room_allocation_respects_budget_ceiling():
    rooms = [
        {"id": 1, "room_number": "101", "capacity_adults": 3, "price_per_night": "100"},
        {"id": 2, "room_number": "102", "capacity_adults": 4, "price_per_night": "120"},
        {"id": 3, "room_number": "103", "capacity_adults": 7, "price_per_night": "300"},
    ]

    selection, total_price = _best_room_allocation(rooms, 7, nights=2, budget_max=240)

    assert selection == []
    assert total_price is None
