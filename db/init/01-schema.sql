BEGIN;
CREATE TABLE app_user (
 user_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 username varchar(30) NOT NULL UNIQUE,
 email varchar(254) NOT NULL UNIQUE,
 password_hash text NOT NULL,
 is_admin boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE fitness_profile (
 user_id bigint PRIMARY KEY REFERENCES app_user(user_id) ON DELETE CASCADE,
 height_cm numeric(5,1) CHECK(height_cm BETWEEN 50 AND 300),
 goal text CHECK(goal IN ('strength','muscle_gain','fat_loss','general_fitness')),
 experience text CHECK(experience IN ('beginner','intermediate','advanced'))
);
CREATE TABLE exercise (
 exercise_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 name varchar(100) NOT NULL UNIQUE,
 muscle_group varchar(40) NOT NULL,
 equipment varchar(40) NOT NULL,
 description text NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 tracking_type text NOT NULL DEFAULT 'reps' CHECK(tracking_type IN ('reps','duration'))
);
CREATE TABLE workout_plan (
 plan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id bigint NOT NULL REFERENCES app_user(user_id),
 name varchar(100) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workout_plan_owner_idx ON workout_plan(owner_id);
CREATE TABLE workout_day (
 day_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 plan_id bigint NOT NULL REFERENCES workout_plan(plan_id) ON DELETE CASCADE,
 name varchar(100) NOT NULL,
 position integer NOT NULL CHECK(position > 0),
 UNIQUE(plan_id, position)
);
CREATE TABLE plan_exercise (
 plan_exercise_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 day_id bigint NOT NULL REFERENCES workout_day(day_id) ON DELETE CASCADE,
 exercise_id bigint NOT NULL REFERENCES exercise(exercise_id),
 position integer NOT NULL CHECK(position > 0),
 target_sets integer NOT NULL CHECK(target_sets BETWEEN 1 AND 30),
 reps_min integer NOT NULL CHECK(reps_min BETWEEN 1 AND 1000),
 reps_max integer NOT NULL CHECK(reps_max BETWEEN reps_min AND 1000),
 UNIQUE(day_id, position)
);
CREATE TABLE workout_session (
 session_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id),
 day_id bigint REFERENCES workout_day(day_id) ON DELETE SET NULL,
 started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz CHECK(finished_at >= started_at),
 notes text NOT NULL DEFAULT ''
);
CREATE INDEX workout_session_user_idx ON workout_session(user_id);
CREATE TABLE set_log (
 set_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 session_id bigint NOT NULL REFERENCES workout_session(session_id) ON DELETE CASCADE,
 exercise_id bigint NOT NULL REFERENCES exercise(exercise_id),
 set_number integer NOT NULL CHECK(set_number > 0),
 reps integer NOT NULL CHECK(reps BETWEEN 0 AND 1000),
 weight_kg numeric(7,2) CHECK(weight_kg BETWEEN 0 AND 10000),
 rir integer CHECK(rir BETWEEN 0 AND 10),
 UNIQUE(session_id, exercise_id, set_number)
);
CREATE TABLE body_measurement (
 measurement_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id),
 measured_on date NOT NULL DEFAULT current_date,
 weight_kg numeric(5,2) NOT NULL CHECK(weight_kg BETWEEN 20 AND 500),
 UNIQUE(user_id, measured_on)
);
CREATE TABLE coach_message (
 message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id bigint NOT NULL REFERENCES app_user(user_id),
 role text NOT NULL CHECK(role IN ('user','assistant')),
 content text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX coach_message_user_idx ON coach_message(user_id);
INSERT INTO exercise(name,muscle_group,equipment,description) VALUES
('Pull-up','Back','Pull-up bar','Pull your body towards the bar with a controlled range of motion.'),
('Push-up','Chest','Bodyweight','Keep your trunk stable while lowering and pressing your body.'),
('Dip','Chest','Parallel bars','Lower and press between parallel bars using a comfortable range.'),
('Bodyweight squat','Legs','Bodyweight','Sit down between your hips and stand with control.'),
('Plank','Core','Bodyweight','Maintain a stable trunk while breathing normally.'),
('Bench press','Chest','Barbell','Press the bar from your chest with controlled technique.'),
('Romanian deadlift','Legs','Barbell','Hinge at the hips while maintaining a stable trunk.'),
('Dumbbell row','Back','Dumbbell','Pull the weight towards your hip while keeping your torso stable.'),
('Overhead press','Shoulders','Dumbbell','Press overhead through a comfortable range.'),
('Skull crusher','Triceps','EZ bar','Extend your elbows with controlled movement.');
UPDATE exercise SET tracking_type='duration' WHERE name='Plank';
CREATE TABLE schema_migration (
 version text PRIMARY KEY,
 applied_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
