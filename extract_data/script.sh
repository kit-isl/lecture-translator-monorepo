
target_date=${1:-2024-05-14}
user=${2:-admin@example.com}

rm -rf data
mkdir -p data

cd /logs/archive/home/$user

find ./ -type d -newermt "$target_date" ! -newermt "$target_date + 1 day" | grep -o '[^/]*$' | \
while read -r dir
do
    echo DIR $dir
    python /src/script.py "$dir/" "/src/data/$dir"
done

