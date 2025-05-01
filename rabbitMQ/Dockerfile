FROM rabbitmq:management

COPY rabbitmq.conf /etc/rabbitmq/rabbitmq.conf

# consistent hashing is required for the application
RUN rabbitmq-plugins enable --offline rabbitmq_consistent_hash_exchange rabbitmq_management
