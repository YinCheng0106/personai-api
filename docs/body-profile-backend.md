# Flexible Body Composition Profile backend contract

`/body-profile` is the preferred architecture. `body_profiles` stores optional
current Basic Profile values, while `body_measurements` stores dated real-world
composition events. Creating or correcting a measurement through the preferred
measurement API never changes the Basic Profile. The legacy `inbody_profiles` table
and `/inbody/me` response shape remain available during this milestone; its POST
adapter also synchronizes submitted height and weight into the Basic Profile for
existing-client compatibility.

The authenticated contract is:

- `GET /body-profile/me`: returns `unknown`, `basic`, or `measured` with nullable
  current fields.
- `PATCH /body-profile/me`: omitted fields remain unchanged and explicit `null`
  clears a field; an all-null Basic Profile row is removed.
- `POST` and `GET /body-profile/me/measurements`: create and list dated history.
- `GET` and `PATCH /body-profile/me/measurements/{measurement_id}`: retrieve or
  correct an owned measurement. Deletion is intentionally not exposed.

Unknown numeric values are represented as `null`. BMI is derived only from current
height and weight. Measurement BMI, lean body mass, and formula-estimated BMR are
derived at response time and are not persisted. Lean body mass and BMR are returned
only when a measurement contains both weight and measured body-fat percentage.

Workout calories are also nullable. Summary `total_calories` is returned only when
every workout in that summary group has calorie data. `calorie_session_count` or
`calorie_workout_count` reports coverage separately from the total workout count;
historical numeric zero values remain numeric zero values.

The frontend phase must render nullable live, workout, and aggregate calories as
unavailable (for example, `—`) and must not coerce them to zero. It should also use
the calorie coverage count when explaining incomplete summaries.

The fixed four-second-per-repetition estimate remains unchanged in this backend
foundation milestone.
