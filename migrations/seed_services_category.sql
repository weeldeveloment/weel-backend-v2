-- Step 1: Add category_key column
ALTER TABLE public.services ADD COLUMN IF NOT EXISTS category_key TEXT;

-- Step 2: Update existing 72 room-level services with category_key and ensure type includes 'room'
UPDATE public.services SET type = '{apartment,cottage,room}' WHERE type IS NULL OR type = '{apartment,cottage}';

-- Step 3: Assign category_key to existing 72 services
UPDATE public.services SET category_key = 'climate_comfort' WHERE title IN ('Air conditioner', 'Heating', 'Fan');
UPDATE public.services SET category_key = 'bathroom' WHERE title IN ('Shower', 'Soap', 'Toilet paper', 'Towels');
UPDATE public.services SET category_key = 'kitchen' WHERE title IN ('A microwave', 'Coffee machine', 'Dining table', 'Dishwasher', 'Fridge', 'Fully equipped kitchen', 'Gas stove', 'Mini kitchen', 'Oven', 'Pots and pans', 'Tableware and cutlery', 'The blender', 'The toaster');
UPDATE public.services SET category_key = 'furniture' WHERE title IN ('Bed linen', 'Clothes dryer', 'Drying machine', 'The cot');
UPDATE public.services SET category_key = 'technology' WHERE title IN ('Computer', 'Smart TV', 'Wi-Fi', 'Workplace');
UPDATE public.services SET category_key = 'safety' WHERE title IN ('Safe deposit', 'Security cameras', 'Sockets near the bed', 'The ironing board');
UPDATE public.services SET category_key = 'view' WHERE title = 'Mountain view';
UPDATE public.services SET category_key = 'pool' WHERE title IN ('Beach', 'Summer pool', 'Winter pool');
UPDATE public.services SET category_key = 'spa_wellness' WHERE title IN ('Jacuzzi', 'Sauna / steam room');
UPDATE public.services SET category_key = 'fitness' WHERE title IN ('Gym', 'Volleyball', 'Basketball Hoop', 'Football area', 'Golf', 'Table tennis');
UPDATE public.services SET category_key = 'entertainment' WHERE title IN ('Billiard', 'Darts', 'Entertainments', 'Karaoke', 'PlayStation', 'Table games', 'VR glasses');
UPDATE public.services SET category_key = 'services' WHERE title IN ('Iron', 'Hygiene products');
UPDATE public.services SET category_key = 'transport_parking' WHERE title IN ('Parkovka', 'Garage');
UPDATE public.services SET category_key = 'outdoor' WHERE title IN ('A place for prayer reading', 'Barbekyu', 'Barbecue', 'Charging for electric vehicles', 'Fireplace', 'Grill', 'Outdoor cuisine', 'Outdoor recreation area', 'Pavilion', 'Terrace', 'The hearth', 'Vacuum cleaner', 'Washer', 'Water filter');

-- Step 4: Mark overlapping items as also applicable to hotel
UPDATE public.services SET type = '{apartment,cottage,room,hotel}' WHERE title IN (
  'Wi-Fi', 'Safe deposit', 'Charging for electric vehicles', 'Parkovka', 'Garage',
  'Summer pool', 'Winter pool', 'Beach', 'Sauna / steam room', 'Jacuzzi',
  'Gym', 'Billiard', 'Table tennis', 'Darts', 'Karaoke', 'PlayStation',
  'Volleyball', 'Basketball Hoop', 'Football area', 'Golf', 'Table games',
  'Terrace', 'Pavilion', 'Outdoor cuisine', 'Outdoor recreation area',
  'Fireplace', 'The hearth', 'Grill', 'Barbecue', 'Barbekyu',
  'Shower', 'Towels', 'Soap', 'Toilet paper', 'Bed linen',
  'Smart TV', 'Computer', 'Workplace', 'Fridge', 'A microwave',
  'Coffee machine', 'Dining table', 'Dishwasher', 'Gas stove',
  'Mini kitchen', 'Oven', 'Pots and pans', 'Tableware and cutlery',
  'The blender', 'The toaster', 'Clothes dryer', 'Drying machine',
  'Iron', 'Hygiene products', 'Vacuum cleaner', 'Washer', 'Water filter',
  'The cot', 'The ironing board', 'Sockets near the bed', 'Security cameras',
  'Mountain view', 'A place for prayer reading', 'VR glasses', 'Entertainments'
);

-- Step 5: Insert hotel-specific services
INSERT INTO public.services (id, title, title_ru, icon_url, type, category_key) VALUES
  (gen_random_uuid(), '24-hour front desk', 'Круглосуточная стойка регистрации', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), '24-hour security', 'Круглосуточная охрана', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Alarm system', 'Сигнализация', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Smoke detectors', 'Дымовые детекторы', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Video surveillance', 'Видеонаблюдение', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Fire extinguishers', 'Огнетушители', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Key/card access', 'Ключ/карта доступа', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Carbon monoxide sensor', 'Датчик угарного газа', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Express check-in/out', 'Экспресс заезд/выезд', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Contactless check-in/out', 'Бесконтактный заезд/выезд', 'property/icons/default.svg', '{hotel}', 'reception_security'),
  (gen_random_uuid(), 'Restaurant', 'Ресторан', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Bar', 'Бар', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Water in room', 'Вода в номере', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'On-site coffee shop', 'Кофейня на территории', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Diner/Snack bar', 'Закусочная/снек-бар', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Vending machine', 'Торговый автомат', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Shared kitchen', 'Общая кухня', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Breakfast in room', 'Завтрак в номер', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Special menu', 'Специальное меню', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Children''s menu', 'Детское меню', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Vegetarian/Vegan menu', 'Вегетарианское/Веган меню', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Grocery store', 'Продуктовый магазин', 'property/icons/default.svg', '{hotel}', 'food_drink'),
  (gen_random_uuid(), 'Free airport transfer', 'Бесплатный трансфер из аэропорта', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Paid airport transfer', 'Платный трансфер из аэропорта', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Shuttle service', 'Шаттл', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Ski shuttle', 'Шаттл до лыжных трасс', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Car rental', 'Аренда автомобилей', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Surface parking', 'Открытая парковка', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Covered parking', 'Крытая парковка', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Disabled parking', 'Парковка для инвалидов', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Luggage storage', 'Камера хранения', 'property/icons/default.svg', '{hotel}', 'transport_parking'),
  (gen_random_uuid(), 'Shared pool', 'Общий бассейн', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Outdoor pool', 'Открытый бассейн', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Indoor pool', 'Закрытый бассейн', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Children''s pool', 'Детский бассейн', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Rooftop pool', 'Бассейн на крыше', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Infinity pool', 'Бассейн с видом на горизонт', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Public bathhouse', 'Общая баня', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'Water park', 'Водный парк', 'property/icons/default.svg', '{hotel}', 'pool'),
  (gen_random_uuid(), 'SPA center', 'СПА центр', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Hammam', 'Хаммам', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Solarium', 'Солярий', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Relaxation area', 'Зона отдыха', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Massage', 'Массаж', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Body treatments', 'Уход за телом', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Manicure/Pedicure', 'Маникюр/Педикюр', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Hairdresser', 'Парикмахер', 'property/icons/default.svg', '{hotel}', 'spa_wellness'),
  (gen_random_uuid(), 'Fitness center', 'Фитнес центр', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Yoga', 'Йога', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Personal trainer', 'Персональный тренер', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Tennis court', 'Теннисный корт', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Badminton', 'Бадминтон', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Mini golf', 'Мини-гольф', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Bicycles', 'Велосипеды', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Horse riding', 'Верховая езда', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Fishing', 'Рыбалка', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Hiking', 'Пешие прогулки', 'property/icons/default.svg', '{hotel}', 'fitness'),
  (gen_random_uuid(), 'Play room', 'Игровая комната', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Evening entertainment', 'Вечерние развлечения', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Night club/DJ', 'Ночной клуб/DJ', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Live music', 'Живая музыка', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Sports broadcast', 'Трансляция спорта', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Stand-up comedy', 'Стендап', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Themed dinners', 'Тематические ужины', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Film screenings', 'Показ фильмов', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Cooking classes', 'Кулинарные классы', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Guided tours', 'Экскурсии с гидом', 'property/icons/default.svg', '{hotel}', 'entertainment'),
  (gen_random_uuid(), 'Family rooms', 'Семейные номера', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Kids'' club', 'Детский клуб', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Playground', 'Детская площадка', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Children''s TV', 'Детское ТВ', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Baby food', 'Детское питание', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Strollers', 'Коляски', 'property/icons/default.svg', '{hotel}', 'for_children'),
  (gen_random_uuid(), 'Concierge', 'Консьерж', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Dry cleaning', 'Химчистка', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Laundry', 'Прачечная', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Ironing', 'Глажка', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Shoe cleaning', 'Чистка обуви', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'ATM', 'Банкомат', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Currency exchange', 'Обмен валюты', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Elevator', 'Лифт', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Business center', 'Бизнес центр', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Conference facilities', 'Конференц-зал', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Tour desk', 'Тур-стол', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Daily cleaning', 'Ежедневная уборка', 'property/icons/default.svg', '{hotel}', 'services'),
  (gen_random_uuid(), 'Disabled rooms', 'Номера для инвалидов', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Hypo-allergenic rooms', 'Гипоаллергенные номера', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Wheelchair accessible', 'Доступ для инвалидных колясок', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Handrails', 'Поручни', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'High toilet', 'Высокий унитаз', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Low sink', 'Низкая раковина', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Emergency cord', 'Аварийный шнур', 'property/icons/default.svg', '{hotel}', 'accessibility'),
  (gen_random_uuid(), 'Braille signs', 'Таблички Брайля', 'property/icons/default.svg', '{hotel}', 'accessibility');
