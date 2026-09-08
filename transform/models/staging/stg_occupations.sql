select *
from {{ source('skills', 'occupations') }}