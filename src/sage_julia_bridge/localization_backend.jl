"""
Construct the retained Oscar realization of a residue map from a localization.

This adapter is deliberately separate from the universal remote-object runtime.
It uses Oscar's public map constructor and the numerator/denominator protocol
implemented by supported localized rings.
"""
struct BridgeLocalizedIdeal
    ring
    generators
end

bridge_localized_ideal(ring, generators) = BridgeLocalizedIdeal(ring, generators)

function bridge_fraction_residue_map(domain, codomain, base_map)
    return map_from_func(
        domain,
        codomain,
        value -> begin
            numerator_image = codomain(base_map(numerator(value)))
            denominator_image = codomain(base_map(denominator(value)))
            numerator_image / denominator_image
        end,
    )
end
