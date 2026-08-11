-- Gives the hotel-only services an icon.
--
-- `seed_services_category.sql` inserted all 87 hotel-specific services with
-- 'property/icons/default.svg' hardcoded, so every hotel amenity except the
-- handful shared with apartments/cottages renders as a blank placeholder in
-- the app. The 72 older services do have icons — this reuses those already
-- uploaded files for the hotel amenities that mean the same thing, rather
-- than waiting on a new icon set.
--
-- Only rows still sitting on the default icon are touched, so re-running is
-- a no-op and anything given a real icon later is left alone. Amenities with
-- no sensible equivalent (accessibility, ATM, elevator, concierge, smoke
-- detectors, …) keep the default until someone uploads artwork for them.
--
-- Preview before writing:
--   SELECT s.title AS amenity, m.source_title AS takes_icon_from
--   FROM public.services s JOIN (<the mapping below>) m ON s.title = m.target_title
--   WHERE s.icon_url LIKE '%default.svg';

BEGIN;

WITH mapping(target_title, source_title) AS (VALUES
    -- Reception & security
    ('Video surveillance',        'Security cameras'),
    ('24-hour security',          'Security cameras'),
    ('Alarm system',              'Security cameras'),

    -- Transport & parking
    ('Parkovka',                  'Garage'),
    ('Surface parking',           'Garage'),
    ('Covered parking',           'Garage'),
    ('Disabled parking',          'Garage'),
    ('Car rental',                'Charging for electric vehicles'),
    ('Free airport transfer',     'Charging for electric vehicles'),
    ('Paid airport transfer',     'Charging for electric vehicles'),
    ('Shuttle service',           'Charging for electric vehicles'),
    ('Ski shuttle',               'Charging for electric vehicles'),

    -- Food & drink
    ('Restaurant',                'Tableware and cutlery'),
    ('Diner/Snack bar',           'Tableware and cutlery'),
    ('Breakfast in room',         'Tableware and cutlery'),
    ('Children''s menu',          'Tableware and cutlery'),
    ('Special menu',              'Tableware and cutlery'),
    ('Vegetarian/Vegan menu',     'Tableware and cutlery'),
    ('On-site coffee shop',       'Coffee machine'),
    ('Shared kitchen',            'Fully equipped kitchen'),
    ('Water in room',             'Water filter'),

    -- Pools & bathhouse
    ('Indoor pool',               'Winter pool'),
    ('Outdoor pool',              'Summer pool'),
    ('Rooftop pool',              'Summer pool'),
    ('Infinity pool',             'Summer pool'),
    ('Children''s pool',          'Summer pool'),
    ('Shared pool',               'Summer pool'),
    ('Water park',                'Summer pool'),
    ('Public bathhouse',          'Sauna / steam room'),
    ('Hammam',                    'Sauna / steam room'),
    ('SPA center',                'Sauna / steam room'),
    ('Relaxation area',           'Outdoor recreation area'),

    -- Fitness
    ('Fitness center',            'Gym'),
    ('Personal trainer',          'Gym'),
    ('Tennis court',              'Table tennis'),
    ('Mini golf',                 'Golf'),

    -- Entertainment
    ('Live music',                'Karaoke'),
    ('Night club/DJ',             'Karaoke'),
    ('Film screenings',           'Home Cinema'),
    ('Sports broadcast',          'Smart TV'),
    ('Play room',                 'Table games'),
    ('Evening entertainment',     'Entertainments'),
    ('Cooking classes',           'Pots and pans'),

    -- For children
    ('Children''s TV',            'Smart TV'),
    ('Playground',                'Outdoor recreation area'),
    ('Family rooms',              'The cot'),
    ('Kids'' club',               'Table games'),

    -- Services
    ('Ironing',                   'Iron'),
    ('Laundry',                   'Washer'),
    ('Dry cleaning',              'Drying machine'),
    ('Daily cleaning',            'Vacuum cleaner'),
    ('Business center',           'Workplace'),
    ('Conference facilities',     'Workplace')
),
-- One row per source title: 'Coffee machine' exists twice, and picking
-- arbitrarily would make the result depend on scan order.
source AS (
    SELECT DISTINCT ON (title) title, icon_url
    FROM public.services
    WHERE icon_url IS NOT NULL
      AND icon_url <> ''
      AND icon_url NOT LIKE '%default.svg'
    ORDER BY title, id
)
UPDATE public.services s
SET icon_url = source.icon_url,
    updated_at = NOW()
FROM mapping m
JOIN source ON source.title = m.source_title
WHERE s.title = m.target_title
  AND s.icon_url LIKE '%default.svg';

COMMIT;

-- How many hotel amenities still have no icon:
--   SELECT count(*) FROM public.services
--   WHERE 'hotel' = ANY(type) AND icon_url LIKE '%default.svg';
