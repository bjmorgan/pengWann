{% if objtype == 'property' %}
:orphan:
{% endif %}

{{ fullname | escape | underline}}

.. currentmodule:: {{ module }}

.. auto{{ objtype }}:: {{ fullname | replace("pengwann.", "pengwann::") }}
